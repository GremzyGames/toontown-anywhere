"""Modern boss-battle gag-selection UI.

Additive overlay on TownBattle. Legacy panels stay loaded and are only hidden
while this UI is up. Config tta-battle-overhaul=0 never constructs it.

Network traffic uses the existing battle events, plus requestConfirm for the
8-second undo window (server-authoritative). Gag icons are the inventory's
existing artwork. Panel art is generated here and is not a replacement for
those icons.
"""
import os
from panda3d.core import *
from direct.gui.DirectGui import *
from direct.interval.IntervalGlobal import Sequence, LerpScaleInterval, Parallel, Func
from direct.showbase.DirectObject import DirectObject
from direct.directnotify import DirectNotifyGlobal

from toontown.toonbase import ToontownBattleGlobals as TBG
from toontown.toonbase import TTLocalizer, ToontownGlobals
from toontown.battle import BattleBase
from toontown.battle import SuitBattleGlobals
from toontown.suit import SuitDNA
from toontown.battle.BattleSelectionMath import (
    HEAL, TRAP, LURE, SOUND, DROP,
    accuracy as preview_accuracy,
    block_damage,
    effect_notes,
    split_amount,
    team_summary,
)
from toontown.toon import NPCToons

notify = DirectNotifyGlobal.directNotify.newCategory('BattleOverhaulUI')


def _load_packed_texture(filename):
    """Load a jpg shipped next to this module. Used for the Cog gear plate only."""
    data = None
    try:
        from importlib.resources import files
        data = files('toontown.battle.ui_art').joinpath(filename).read_bytes()
    except Exception:
        try:
            path = os.path.join(os.path.dirname(__file__), 'ui_art', filename)
            with open(path, 'rb') as handle:
                data = handle.read()
        except Exception:
            return None
    if not data:
        return None
    try:
        image = PNMImage()
        if not image.read(StringStream(data)):
            return None
        tex = Texture(filename)
        tex.load(image)
        tex.setMinfilter(Texture.FTLinear)
        tex.setMagfilter(Texture.FTLinear)
        return tex
    except Exception:
        return None

OVERLAY_SORT = 200
PANEL_SORT = 210
TARGET_SORT = 220
NOTICE_SORT = 230
TIP_SORT = 240
DIALOG_SORT = 250

# Stable, distinguishable colors. Index is the battle roster slot, which the
# server only appends to, so a Toon's color does not move when someone joins.
PLAYER_COLORS = (
    (0.95, 0.35, 0.35),
    (0.30, 0.65, 1.00),
    (0.35, 0.85, 0.45),
    (1.00, 0.75, 0.25),
    (0.75, 0.45, 0.95),
    (0.20, 0.85, 0.80),
    (1.00, 0.50, 0.70),
    (0.85, 0.85, 0.40),
)

# Track index -> (kind key, short label). Unknown tracks fall back to their name.
TRACK_KIND = {
    0: ('heal', 'Heal'),
    1: ('trap', 'Trap'),
    2: ('lure', 'Lure'),
    3: ('sound', 'Sound'),
    4: ('throw', 'Throw'),
    5: ('squirt', 'Squirt'),
    6: ('drop', 'Drop'),
}

ACTION_TIPS = {
    'Run': 'Leave the battle. You will be asked to confirm before anyone runs.',
    'SOS': 'Call an SOS card. Its effect is the card you pick.',
    'Block': 'Brace for this round. Incoming hits are reduced, then the stance ends.',
    'Pass': 'Take no gag this round.',
    'Fire': 'Use a pink slip. This still uses the existing fire choice.',
}


def _kind(track):
    if track in TRACK_KIND:
        return TRACK_KIND[track]
    try:
        name = str(TBG.Tracks[track])
    except Exception:
        name = 'Gag'
    return (name.lower(), name.capitalize())


def _ui_volume():
    """Battle-click loudness. Never writes the music or world managers."""
    try:
        store = platform.window.localStorage
        if store.getItem('tta_ui_mute') == '1':
            return 0.0
        raw = store.getItem('tta_ui_volume')
        if raw not in (None, ''):
            return max(0.0, min(1.0, int(raw) / 100.0))
    except Exception:
        pass
    return 1.0


def _input_method():
    try:
        store = platform.window.localStorage
        override = store.getItem('tta_input_method') or 'auto'
        if override in ('mouse', 'touch', 'controller'):
            return override
        live = str(getattr(platform.window.tta, 'inputMethod', '') or '')
        if live in ('mouse', 'touch', 'controller'):
            return live
        if getattr(platform.window.tta, 'touchScreen', False):
            return 'touch'
    except Exception:
        pass
    return 'mouse'


def _panel_texture(width, height, fill, border, radius=36):
    """Panel art only. Sized for the tray, not stretched across the screen."""
    img = PNMImage(int(width), int(height))
    img.addAlpha()
    fr, fg, fb, fa = fill
    br, bgc, bb, ba = border
    width = int(width)
    height = int(height)
    for y in range(height):
        for x in range(width):
            dx = min(x, width - 1 - x)
            dy = min(y, height - 1 - y)
            inside = dx >= radius or dy >= radius or ((radius - dx) ** 2 + (radius - dy) ** 2) <= radius * radius
            if not inside:
                img.setAlpha(x, y, 0)
                continue
            inset = 10
            inner = (dx >= radius + inset or dy >= radius + inset or
                     ((radius + inset - dx) ** 2 + (radius + inset - dy) ** 2) <= (radius - 2) ** 2)
            grain = 0.97 + 0.03 * (((x * 13 + y * 7) & 15) / 15.0)
            shade = (1.0 - 0.18 * (y / float(height))) * grain
            if not inner:
                img.setXelA(x, y, br * shade, bgc * shade, bb * shade, ba)
            elif dy < 14:
                img.setXelA(x, y, min(1, fr * 1.15), min(1, fg * 1.12), min(1, fb * 1.08), fa)
            else:
                img.setXelA(x, y, fr * shade, fg * shade, fb * shade, fa)
    tex = Texture('tta-battle-ui')
    tex.load(img)
    tex.setMinfilter(Texture.FTLinear)
    tex.setMagfilter(Texture.FTLinear)
    tex.setWrapU(Texture.WMClamp)
    tex.setWrapV(Texture.WMClamp)
    return tex


class BattleOverhaulUI(DirectObject):
    def __init__(self, townBattle):
        DirectObject.__init__(self)
        self.tb = townBattle
        self.enabled = True
        self.visible = False
        self.loaded = False
        self.trackRows = {}
        self.teammatePanels = []
        self.targetCards = []
        self.actionButtons = []
        self.sosCards = []
        self.focusItems = []
        self.focusIndex = 0
        self.longPressTask = None
        self.longPressFired = False
        self.noticeTask = None
        self.undoTask = None
        self.pulseTask = None
        self.suits = []
        self.toons = []
        self.lured = []
        self.trapped = []
        self.localNum = 0
        self.chosen = ([], [], [], [])
        self.roster = []
        self.undoLeft = {}
        self.pending = None
        self.locked = False
        self.keepSelection = False
        self.scrollIndex = 0
        self.inventorySig = None
        self.shownConflicts = set()
        self.prevTargets = {}
        self.turnTime = None
        self.blockCooldown = 0
        self._blockRoundOpen = False
        self.mobile = False
        self.teamExpanded = False
        self.actionsExpanded = False
        self.sosOpen = False
        self.runOpen = False
        self.targetMode = None
        self.textures = []
        self.sounds = {}
        self._lastComboNote = None

    # ------------------------------------------------------------------ lifecycle
    def load(self):
        if self.loaded:
            return
        self.loaded = True
        self._loadSounds()
        self.root = DirectFrame(parent=base.aspect2d, relief=None, sortOrder=0)
        self.root.setBin('gui-popup', OVERLAY_SORT)
        self.root.setDepthTest(False)
        self.root.setDepthWrite(False)
        for attr in ('runPanel', 'SOSPanel', 'SOSPetSearchPanel',
                     'SOSPetInfoPanel', 'FireCogPanel', 'timer'):
            try:
                obj = getattr(self.tb, attr, None)
                if obj is not None:
                    obj.setBin('gui-popup', DIALOG_SORT)
            except Exception:
                pass
        self.cluster = self.root.attachNewNode('tta-battle-cluster')
        # The first thing drawn is the bottom bar, not two tall boxes.
        self.cluster.setPos(0, 0, -0.52)
        self._buildChrome()
        self._buildLeftPanel()
        self._buildCenterPanel()
        self._buildRightPanel()
        self._buildDetailPanel()
        # The three panels are the screen. Later pieces must not remove them.
        try:
            self._applyLayout()
        except Exception as exc:
            print('web: battle overhaul build failed: %s' % exc)
            import traceback
            traceback.print_exc()
        print('web: battle overhaul layout ready')
        for step in (self._buildCogStrip, self._buildTargetOverlay,
                     self._buildRunDialog, self._buildTooltip, self._buildNotification):
            try:
                step()
            except Exception as exc:
                print('web: battle overhaul build failed: %s' % exc)
                import traceback
                traceback.print_exc()
        try:
            self._applyLayout()
        except Exception as exc:
            print('web: battle overhaul build failed: %s' % exc)

    def prepare(self):
        """Build the screen but keep it hidden until gag selection starts."""
        self.load()
        self.visible = False
        self.root.hide()

    def enter(self):
        self.present()

    def present(self):
        self.load()
        self.visible = True
        self.root.show()
        self._wire()
        self._applyLayout()
        if not self.toons:
            try:
                self.toons = [base.localAvatar]
            except Exception:
                pass
        self.beginRound()
        if not self.toons:
            try:
                self.toons = [base.localAvatar]
            except Exception:
                pass
        self.refreshAll()
        print('web: battle overhaul UI presented tracks=%d actions=%d' % (
            len(self.trackRows), len(self.actionButtons)))

    def conceal(self):
        self.visible = False
        if self.loaded:
            self.root.hide()
        self._unwire()
        self._clearTooltip()

    def exit(self):
        if not self.loaded:
            return
        self.visible = False
        self.root.hide()
        self._unwire()
        self._clearTooltip()
        self._clearNotification()
        self._stopPulse()

    def destroy(self):
        self._unwire()
        for name in ('longPressTask', 'noticeTask', 'undoTask', 'pulseTask'):
            task = getattr(self, name, None)
            if task:
                taskMgr.remove(task)
                setattr(self, name, None)
        if self.loaded:
            self.root.destroy()
            self.loaded = False
        self.trackRows = {}
        self.teammatePanels = []
        self.targetCards = []
        self.actionButtons = []
        self.sosCards = []
        self.focusItems = []

    def beginRound(self):
        """New turn. Does not carry a gag, target, confirm, or undo forward."""
        self.pending = None
        self.locked = False
        self.keepSelection = False
        self.undoLeft = {}
        self.shownConflicts = set()
        self.prevTargets = {}
        self.sosOpen = False
        self.runOpen = False
        self.targetMode = None
        self._lastComboNote = None
        self._setDetails('')
        try:
            base.localAvatar.ttaBlockActive = False
        except Exception:
            pass
        if not self.toons:
            try:
                self.toons = [base.localAvatar]
            except Exception:
                pass
        if self.loaded:
            for node_name in ('targetPanel', 'runDialog', 'sosFrame'):
                node = getattr(self, node_name, None)
                if node is not None:
                    node.hide()
            self._clearTooltip()

    def _wire(self):
        for ev, cb, args in (
                ('arrow_left', self._moveFocus, [-1, 0]),
                ('arrow_right', self._moveFocus, [1, 0]),
                ('arrow_up', self._moveFocus, [0, 1]),
                ('arrow_down', self._moveFocus, [0, -1]),
                ('enter', self._activateFocus, []),
                ('escape', self._cancelFocus, []),
                ('wheel_up', self._scroll, [1]),
                ('wheel_down', self._scroll, [-1])):
            self.accept(ev, cb, args)

    def _unwire(self):
        for ev in ('arrow_left', 'arrow_right', 'arrow_up', 'arrow_down',
                   'enter', 'escape', 'wheel_up', 'wheel_down'):
            self.ignore(ev)

    def _loadSounds(self):
        for key, path in (
                ('click', 'phase_3/audio/sfx/GUI_create_toon_fwd.ogg'),
                ('back', 'phase_3/audio/sfx/GUI_create_toon_back.ogg'),
                ('pop', 'phase_3/audio/sfx/GUI_balloon_popup.ogg')):
            try:
                self.sounds[key] = loader.loadSfx(path)
            except Exception:
                self.sounds[key] = None

    def _swallowLongPress(self):
        if self.longPressFired:
            self.longPressFired = False
            return True
        return False

    def _play(self, key):
        vol = _ui_volume()
        if vol <= 0:
            return
        sound = self.sounds.get(key)
        if sound is None:
            return
        try:
            sound.setVolume(vol)
            sound.play()
        except Exception:
            pass

    # ------------------------------------------------------------------ layout
    def _tex(self, fill, border, wide=480, tall=360):
        tex = _panel_texture(wide, tall, fill, border)
        self.textures.append(tex)
        return tex

    def _box(self, parent, half_x, half_z, fill, border, pos=(0, 0, 0)):
        # Quiet panel with a light outline so the three columns read as one set.
        edge = 0.014
        shell = DirectFrame(parent=parent, relief=None, pos=pos)
        outline = DirectFrame(parent=shell, relief=DGG.FLAT, frameColor=border,
                              frameSize=(-(half_x + edge), half_x + edge, -(half_z + edge), half_z + edge))
        fill = DirectFrame(parent=shell, relief=DGG.FLAT,
                           frameColor=fill if fill else (0.13, 0.15, 0.18, 0.94),
                           frameSize=(-half_x, half_x, -half_z, half_z))
        shell.setPythonTag('outline', outline)
        shell.setPythonTag('fill', fill)
        shell.setBin('gui-popup', PANEL_SORT)
        return shell

    def _size_shell(self, shell, x, z, hx, hz):
        shell.setPos(x, 0, z)
        edge = 0.014
        outline = shell.getPythonTag('outline') if shell.hasPythonTag('outline') else None
        fill = shell.getPythonTag('fill') if shell.hasPythonTag('fill') else None
        if outline is not None:
            outline['frameSize'] = (-(hx + edge), hx + edge, -(hz + edge), hz + edge)
        if fill is not None:
            fill['frameSize'] = (-hx, hx, -hz, hz)

    def _buildChrome(self):
        # No full-screen plate. The fight stays visible; only the trays draw.
        self.bg = None
        self.youLabel = DirectLabel(parent=self.root, relief=None, text='',
                                    text_scale=0.04, text_fg=(1, 1, 1, 1),
                                    text_shadow=(0, 0, 0, 1),
                                    text_align=TextNode.ALeft, textMayChange=1)
        self.youLabel.setBin('gui-popup', PANEL_SORT)
        self.timerLabel = DirectLabel(parent=self.root, relief=None, text='',
                                      text_scale=0.05, text_fg=(1, 1, 1, 1),
                                      text_shadow=(0, 0, 0, 1),
                                      text_align=TextNode.ACenter, textMayChange=1)
        self.timerLabel.setBin('gui-popup', PANEL_SORT)
        self.hintLabel = DirectLabel(parent=self.root, relief=None, text='',
                                     text_scale=0.034, text_fg=(1, 1, 1, 1),
                                     text_shadow=(0, 0, 0, 1),
                                     text_align=TextNode.ARight, textMayChange=1)
        self.hintLabel.setBin('gui-popup', PANEL_SORT)

    def _buildLeftPanel(self):
        # Shares its right edge with the gags box. Same height, centered group.
        self.leftPanel = self._box(
            self.cluster, 0.28, 0.42,
            (0.11, 0.13, 0.16, 0.96), (0.78, 0.82, 0.88, 1),
            pos=(-1.55, 0, 0))
        self.teamTitle = DirectLabel(parent=self.leftPanel, relief=None, text='Team',
                                     text_scale=0.05, text_fg=(1, 1, 1, 1), pos=(0, 0, 0.42))
        self.teamToggle = DirectButton(
            parent=self.leftPanel, relief=DGG.FLAT, text='Team', text_scale=0.05,
            text_fg=(1, 1, 1, 1), frameColor=(0.2, 0.4, 0.7, 1),
            frameSize=(-0.28, 0.28, -0.06, 0.06), pos=(0, 0, 0.78),
            command=self._toggleTeam)
        self.teamToggle.hide()
        self.teamList = DirectFrame(parent=self.leftPanel, relief=None)

    def _buildCenterPanel(self):
        self.centerPanel = self._box(
            self.cluster, 0.85, 0.42,
            (0.17, 0.19, 0.23, 0.96), (0.78, 0.82, 0.88, 1),
            pos=(0, 0, 0))
        self.gagTitle = DirectLabel(parent=self.centerPanel, relief=None, text='Gags',
                                    text_scale=0.045, text_fg=(0.82, 0.84, 0.88, 1), pos=(0, 0, 0.30))
        self.scroll = None
        # Rows live on the gag panel itself. A scrolled frame here threw and
        # left the two tall boxes on screen with no icons and no actions.
        self.gagRows = DirectFrame(parent=self.centerPanel, relief=None, pos=(0, 0, 0.16))
        self.gagRows.setBin('gui-popup', PANEL_SORT + 5)

    def _buildCogStrip(self):
        # Industrial gears only here. The rest of the interface stays plain.
        gear = _load_packed_texture('cog_gears.jpg')
        kwargs = {}
        if gear is not None:
            kwargs['image'] = gear
            kwargs['image_scale'] = (0.74, 1, 0.16)
        self.cogPanel = DirectFrame(
            parent=self.cluster, relief=DGG.FLAT,
            frameColor=(0.08, 0.09, 0.11, 0.94),
            frameSize=(-0.74, 0.74, -0.16, 0.16),
            pos=(0, 0, 0.84), **kwargs)
        self.cogPanel.setBin('gui-popup', PANEL_SORT)
        self.enemyTitle = DirectLabel(parent=self.cogPanel, relief=None, text='Enemy',
                                      text_scale=0.04, text_fg=(1, 1, 1, 1),
                                      text_align=TextNode.ACenter, pos=(0, 0, 0.04))
        self.cogRow = DirectFrame(parent=self.cogPanel, relief=None)
        self.cogCards = []

    def _refreshCogs(self):
        if not getattr(self, 'cogRow', None):
            return
        for card in self.cogCards:
            card.removeNode()
        for child in list(self.cogRow.getChildren()):
            child.removeNode()
        self.cogCards = []
        live = [(i, suit) for i, suit in enumerate(self.suits) if suit is not None][:4]
        gap = 0.34
        for order, (i, suit) in enumerate(live):
            card = DirectButton(
                parent=self.cogRow, relief=DGG.FLAT,
                text=self._cogPlate(suit),
                text_scale=0.022, text_fg=(0.93, 0.94, 0.96, 1),
                text_align=TextNode.ACenter, text_pos=(0, 0.02),
                frameColor=(0.45, 0.48, 0.52, 0.95),
                frameSize=(-0.16, 0.16, -0.046, 0.046),
                pos=(self._fieldX(order, len(live), gap), 0, -0.02),
                command=self._pickCogIcon, extraArgs=[i])
            card.setBin('gui-popup', PANEL_SORT)
            self._rim(card, 0.16, 0.046)
            card.bind(DGG.ENTER, self._hoverGrow, [card])
            self.cogCards.append(card)
        self._markCogSelection()

    def _cogPlate(self, suit):
        """Name, department, and level, in the same order as the street nameplate."""
        try:
            name = suit.getName()
        except Exception:
            name = 'Cog'
        try:
            kind = suit.getStyleDept()
        except Exception:
            kind = ''
        try:
            level = suit.getActualLevel()
        except Exception:
            level = ''
        return '%s\n%s\nLvl. %s' % (name, kind, level)

    def _fieldX(self, order, count, gap):
        """Screen X for a combatant, left to right as they stand.

        Battle index 0 is the positive-X spot, on the player's right. The
        lists stay in that order so a click still targets the same cog or toon.
        """
        if count <= 1:
            return 0
        origin = -((count - 1) * gap) / 2.0
        return origin + ((count - 1) - order) * gap

    def _buildRightPanel(self):
        # Shares its left edge with the gags box.
        self.rightPanel = self._box(
            self.cluster, 0.42, 0.42,
            (0.11, 0.13, 0.16, 0.96), (0.78, 0.82, 0.88, 1),
            pos=(1.45, 0, 0))
        self.actionTitle = DirectLabel(parent=self.rightPanel, relief=None, text='Actions',
                                       text_scale=0.05, text_fg=(1, 1, 1, 1), pos=(0, 0, 0.42))
        specs = (('Run', self._askRun), ('SOS', self._toggleSos),
                 ('Block', self._chooseBlock), ('Pass', self._choosePass))
        self.actionButtons = []
        for i, (name, cmd) in enumerate(specs):
            col, row = i % 2, i // 2
            b = DirectButton(
                parent=self.rightPanel, relief=DGG.FLAT, text=name, text_scale=0.05,
                text_align=TextNode.ACenter, text_pos=(0, -0.015),
                text_fg=(1, 1, 1, 1), frameColor=(0.28, 0.40, 0.54, 1),
                frameSize=(-0.13, 0.13, -0.055, 0.055),
                pos=(-0.15 + col * 0.30, 0, 0.24 - row * 0.16), command=cmd)
            b.setBin('gui-popup', PANEL_SORT)
            b.bind(DGG.ENTER, self._showActionTip, [name])
            b.bind(DGG.EXIT, self._clearTooltip)
            b.bind(DGG.B1PRESS, self._actionPress, [name])
            self.actionButtons.append(b)
        self.fireButton = DirectButton(
            parent=self.rightPanel, relief=DGG.FLAT, text='Fire', text_scale=0.045,
            text_fg=(1, 1, 1, 1), frameColor=(0.55, 0.25, 0.25, 1),
            frameSize=(-0.16, 0.16, -0.045, 0.045), pos=(0, 0, -0.16),
            command=self._doFire)
        self.fireButton.setBin('gui-popup', PANEL_SORT)
        self.sosFrame = DirectFrame(parent=self.rightPanel, relief=None, pos=(0, 0, -0.02))
        self.sosFrame.hide()
        self.confirmFrame = DirectFrame(parent=self.rightPanel, relief=None, pos=(0, 0, -0.28))
        self.confirmText = DirectLabel(
            parent=self.confirmFrame, relief=None, text='', text_scale=0.038,
            text_fg=(1, 1, 1, 1), text_wordwrap=22, textMayChange=1, pos=(0, 0, 0.16))
        self.confirmCombo = DirectLabel(
            parent=self.confirmFrame, relief=None, text='', text_scale=0.032,
            text_fg=(0.75, 0.95, 0.8, 1), text_wordwrap=24, textMayChange=1, pos=(0, 0, 0.02))
        self.confirmBtn = DirectButton(
            parent=self.confirmFrame, relief=DGG.FLAT, text='Confirm',
            text_scale=0.042, text_fg=(0.75, 0.75, 0.75, 1),
            frameColor=(0.30, 0.32, 0.36, 1),
            frameSize=(-0.28, 0.28, -0.055, 0.055), pos=(0, 0, -0.12),
            command=self._confirmPending, state=DGG.DISABLED)
        self.undoBtn = DirectButton(
            parent=self.confirmFrame, relief=DGG.FLAT, text='Undo',
            text_scale=0.042, text_fg=(1, 1, 1, 1), frameColor=(0.55, 0.35, 0.15, 1),
            frameSize=(-0.22, 0.22, -0.05, 0.05), pos=(0, 0, -0.12),
            command=self._undo)
        self.undoBtn.hide()
        self.mobileConfirm = DirectFrame(parent=base.a2dBottomCenter, relief=None)
        self.mobileConfirm.setBin('gui-popup', NOTICE_SORT)
        self.mobileText = DirectLabel(
            parent=self.mobileConfirm, relief=None, text='', text_scale=0.045,
            text_fg=(1, 1, 1, 1), text_wordwrap=40, textMayChange=1, pos=(0, 0, 0.16))
        self.mobileBtn = DirectButton(
            parent=self.mobileConfirm, relief=DGG.FLAT, text='Confirm',
            text_scale=0.05, text_fg=(0.75, 0.75, 0.75, 1),
            frameColor=(0.30, 0.32, 0.36, 1),
            frameSize=(-0.35, 0.35, -0.06, 0.06), pos=(0.4, 0, 0.05),
            command=self._confirmPending, state=DGG.DISABLED)
        self.mobileUndo = DirectButton(
            parent=self.mobileConfirm, relief=DGG.FLAT, text='Undo',
            text_scale=0.05, text_fg=(1, 1, 1, 1), frameColor=(0.55, 0.35, 0.15, 1),
            frameSize=(-0.25, 0.25, -0.06, 0.06), pos=(-0.45, 0, 0.05),
            command=self._undo)
        self.mobileConfirm.hide()

    def _buildDetailPanel(self):
        # Same dark panel as Actions, on the left. Stays empty until a gag is hovered or chosen.
        self.detailPanel = self._box(
            self.cluster, 0.35, 0.70,
            (0.11, 0.13, 0.16, 0.96), (0.78, 0.82, 0.88, 1),
            pos=(-1.9, 0, 0))
        self.detailTitle = DirectLabel(
            parent=self.detailPanel, relief=None, text='Tooltips',
            text_scale=0.05, text_fg=(1, 1, 1, 1),
            text_align=TextNode.ACenter, pos=(0, 0, 0.42))
        self.detailLabel = DirectLabel(
            parent=self.detailPanel, relief=None, text='',
            text_scale=0.038, text_fg=(0.93, 0.94, 0.96, 1),
            text_align=TextNode.ACenter, text_wordwrap=14, textMayChange=1,
            pos=(0, 0, 0.28))
        self.detailLabel.setBin('gui-popup', PANEL_SORT + 1)
        try:
            self.detailLabel['text_font'] = ToontownGlobals.getInterfaceFont()
        except Exception:
            pass

    def _buildTargetOverlay(self):
        self.targetPanel = DirectFrame(
            parent=self.root, relief=DGG.FLAT,
            frameColor=(0.96, 0.97, 1, 0.96),
            frameSize=(-1.05, 1.05, -0.42, 0.42))
        self.targetPanel.setBin('gui-popup', TARGET_SORT)
        self.targetTitle = DirectLabel(
            parent=self.targetPanel, relief=None, text='Choose a target',
            text_scale=0.055, text_fg=(0.1, 0.18, 0.35, 1), pos=(0, 0, 0.32))
        self.targetBack = DirectButton(
            parent=self.targetPanel, relief=DGG.FLAT, text='Back', text_scale=0.05,
            text_fg=(1, 1, 1, 1), frameColor=(0.35, 0.38, 0.48, 1),
            frameSize=(-0.18, 0.18, -0.055, 0.055),             pos=(-0.82, 0, -0.32),
            command=self._targetBack)
        self.targetEmpty = DirectLabel(
            parent=self.targetPanel, relief=None, text='', text_scale=0.05,
            text_fg=(0.35, 0.15, 0.15, 1), text_wordwrap=28, textMayChange=1, pos=(0, 0, 0))
        self.targetPanel.hide()

    def _buildRunDialog(self):
        self.runDialog = DirectFrame(
            parent=self.root, relief=DGG.FLAT,
            frameColor=(0.16, 0.12, 0.14, 0.96),
            frameSize=(-0.62, 0.62, -0.28, 0.28))
        self.runDialog.setBin('gui-popup', DIALOG_SORT)
        DirectLabel(parent=self.runDialog, relief=None,
                    text='Run away from this battle?', text_scale=0.055,
                    text_fg=(1, 1, 1, 1), pos=(0, 0, 0.16))
        DirectButton(parent=self.runDialog, relief=DGG.FLAT, text='Confirm Escape',
                     text_scale=0.045, text_fg=(1, 1, 1, 1), frameColor=(0.65, 0.25, 0.2, 1),
                     frameSize=(-0.28, 0.28, -0.06, 0.06), pos=(-0.32, 0, -0.12),
                     command=self._confirmRun)
        DirectButton(parent=self.runDialog, relief=DGG.FLAT, text='Cancel',
                     text_scale=0.045, text_fg=(1, 1, 1, 1), frameColor=(0.3, 0.35, 0.45, 1),
                     frameSize=(-0.2, 0.2, -0.06, 0.06), pos=(0.32, 0, -0.12),
                     command=self._cancelRun)
        self.runDialog.hide()

    def _buildTooltip(self):
        self.tip = DirectFrame(
            parent=base.aspect2d, relief=DGG.FLAT,
            frameColor=(0.12, 0.14, 0.18, 0.96),
            frameSize=(-0.32, 0.32, -0.14, 0.14))
        self.tip.setBin('gui-popup', TIP_SORT)
        self.tipLabel = DirectLabel(
            parent=self.tip, relief=None, text='', text_scale=0.032,
            text_fg=(0.92, 0.94, 0.96, 1), text_align=TextNode.ALeft,
            textMayChange=1, text_wordwrap=16, pos=(-0.28, 0, 0.10))
        self._rim(self.tip, 0.32, 0.14)
        self.tip.hide()

    def _buildNotification(self):
        self.notice = DirectLabel(
            parent=base.a2dBottomCenter, relief=None, text='', text_scale=0.048,
            text_fg=(1, 0.92, 0.45, 1), text_shadow=(0, 0, 0, 1),
            text_wordwrap=48, pos=(0, 0, 0.12), textMayChange=1)
        self.notice.setBin('gui-popup', NOTICE_SORT)
        self.notice.hide()

    def _applyLayout(self):
        self.mobile = self._wantMobile()
        if getattr(self, 'hintLabel', None) is not None:
            self.hintLabel['text'] = ''
            self.hintLabel.hide()
        # Fit one connected bar inside the real window. Only the team column is thin.
        try:
            aspect = float(base.getAspectRatio())
        except Exception:
            aspect = 1.33
        if aspect < 1.2:
            aspect = 1.33
        # Gags in the middle. A short cog strip above them. A thin team strip
        # under them. Details on the left and Actions on the right both run
        # from the top of the cog strip to the bottom of the team strip.
        # The three pieces together are centered on the screen.
        detail_w = 0.70
        action_w = 0.88
        gag_w = 1.70
        span = detail_w + gag_w + action_w
        max_span = (2.0 * aspect) - 0.16
        if span > max_span:
            gag_w = max(1.15, gag_w - (span - max_span))
            span = detail_w + gag_w + action_w
        if span > max_span:
            detail_w = max(0.56, detail_w - (span - max_span))
            span = detail_w + gag_w + action_w
        self._gagHalf = gag_w * 0.5
        left = -span * 0.5
        detail_x = left + detail_w * 0.5
        gag_x = left + detail_w + gag_w * 0.5
        action_x = left + detail_w + gag_w + action_w * 0.5
        self._detailW = detail_w
        hz = 0.46
        cog_h = 0.16
        team_h = 0.30
        gap = 0.012
        cog_z = hz + gap + cog_h * 0.5
        team_z = -(hz + gap + team_h * 0.5)
        cog_top = cog_z + cog_h * 0.5
        team_bottom = team_z - team_h * 0.5
        act_h = (cog_top - team_bottom) * 0.5
        act_z = (cog_top + team_bottom) * 0.5
        self._hz = hz
        self._gagX = gag_x
        self.cluster.setPos(0, 0, -0.90 - team_bottom)
        self.cluster.setScale(1)
        if getattr(self, 'centerPanel', None) is not None:
            self._size_shell(self.centerPanel, gag_x, 0, gag_w * 0.5, hz)
        if getattr(self, 'rightPanel', None) is not None:
            self._size_shell(self.rightPanel, action_x, act_z, action_w * 0.5, act_h)
        if getattr(self, 'detailPanel', None) is not None:
            self._size_shell(self.detailPanel, detail_x, act_z, detail_w * 0.5, act_h)
            if getattr(self, 'detailTitle', None) is not None:
                self.detailTitle.setPos(0, 0, hz - 0.07)
                self.detailTitle['text_align'] = TextNode.ACenter
            if getattr(self, 'detailLabel', None) is not None:
                self.detailLabel.setPos(0, 0, hz - 0.18)
                self.detailLabel['text_align'] = TextNode.ACenter
                self.detailLabel['text_wordwrap'] = max(10, int((detail_w - 0.12) / 0.042))
        if getattr(self, 'leftPanel', None) is not None:
            self._size_shell(self.leftPanel, gag_x, team_z, gag_w * 0.5, team_h * 0.5)
        if getattr(self, 'gagRows', None) is not None:
            self.gagRows.setPos(0, 0, hz - 0.16)
        for title_name in ('gagTitle', 'actionTitle'):
            title = getattr(self, title_name, None)
            if title is not None:
                title.setPos(0, 0, hz - 0.07)
        if getattr(self, 'teamTitle', None) is not None and getattr(self, 'leftPanel', None) is not None:
            self.teamTitle.reparentTo(self.leftPanel)
            self.teamTitle['text'] = 'Team'
            self.teamTitle['text_align'] = TextNode.ACenter
            self.teamTitle.setPos(0, 0, team_h * 0.5 - 0.035)
            self.teamTitle.setScale(1)
        if getattr(self, 'enemyTitle', None) is not None:
            self.enemyTitle.setPos(0, 0, cog_h * 0.5 - 0.032)
        try:
            font = ToontownGlobals.getInterfaceFont()
            for lab in (self.teamTitle, self.gagTitle, self.actionTitle, getattr(self, 'detailTitle', None), getattr(self, 'enemyTitle', None)):
                if lab is not None:
                    lab['text_font'] = font
            for button in self.actionButtons:
                button['text_font'] = font
                button['text_align'] = TextNode.ACenter
                button['text_pos'] = (0, -0.015)
        except Exception:
            pass
        if getattr(self, 'cogPanel', None) is not None and getattr(self, 'centerPanel', None) is not None:
            self.cogPanel.setPos(self.centerPanel.getX(), 0, cog_z)
            self.cogPanel['frameSize'] = (-gag_w * 0.5, gag_w * 0.5, -cog_h * 0.5, cog_h * 0.5)
            if self.cogPanel['image'] is not None:
                self.cogPanel['image_scale'] = (gag_w * 0.5, 1, cog_h * 0.5)
            self._rim(self.cogPanel, gag_w * 0.5, cog_h * 0.5)
        slots = ((-0.18, 0.24), (0.18, 0.24), (-0.18, 0.06), (0.18, 0.06))
        for button, (bx, bz) in zip(self.actionButtons, slots):
            button.setPos(bx, 0, bz)
            button['frameSize'] = (-0.15, 0.15, -0.055, 0.055)
            self._rim(button, 0.15, 0.055)
        if getattr(self, 'confirmFrame', None) is not None:
            self.confirmFrame.setPos(0, 0, -0.32)
        if getattr(self, 'targetPanel', None) is not None:
            self.targetPanel.setPos(0, 0, 0)
        if getattr(self, 'runDialog', None) is not None:
            self.runDialog.setPos(0, 0, 0.15)
        if getattr(self, 'youLabel', None) is not None:
            self.youLabel.hide()
        if getattr(self, 'timerLabel', None) is not None and getattr(self, 'centerPanel', None) is not None:
            self.timerLabel.reparentTo(self.centerPanel)
            self.timerLabel.setPos(self._gagHalf - 0.16, 0, hz - 0.08)
            self.timerLabel['text_fg'] = (0.82, 0.84, 0.88, 1)
            self.timerLabel['text_shadow'] = (0, 0, 0, 0)
        if getattr(self, 'hintLabel', None) is not None:
            self.hintLabel['text'] = ''
            self.hintLabel.hide()
        if getattr(self, 'teamToggle', None) is not None:
            self.teamToggle.hide()
        if getattr(self, 'teamList', None) is not None:
            self.teamList.show()
        if self.mobile and getattr(self, 'confirmFrame', None) is not None:
            self.confirmFrame.hide()
        elif getattr(self, 'mobileConfirm', None) is not None:
            self.mobileConfirm.hide()

    def _rim(self, node, hx, hz, t=0.004, color=(0.86, 0.88, 0.92, 1)):
        old = node.getPythonTag('rimNodes') if node.hasPythonTag('rimNodes') else []
        for edge in old:
            edge.removeNode()
        specs = (
            (-hx, hx, hz - t, hz),
            (-hx, hx, -hz, -hz + t),
            (-hx, -hx + t, -hz, hz),
            (hx - t, hx, -hz, hz),
        )
        edges = []
        for frame in specs:
            edge = DirectFrame(parent=node, relief=DGG.FLAT, state=DGG.DISABLED, frameColor=color, frameSize=frame)
            edges.append(edge)
        node.setPythonTag('rimNodes', edges)

    def _hoverGrow(self, node, event=None):
        self._pop(node, 1.0)

    def _wantMobile(self):
        if _input_method() == 'touch':
            return True
        try:
            w = base.win.getXSize()
            h = base.win.getYSize()
            if h and w > h and h <= 520:
                return True
        except Exception:
            pass
        return False

    def _toggleTeam(self):
        self.teamExpanded = not self.teamExpanded
        if self.teamExpanded:
            self.teamList.show()
            self.leftPanel.setScale(1.05)
        else:
            self.teamList.hide()
            self.leftPanel.setScale(0.72)
        self._play('click')

    @staticmethod
    def _arrow(direction):
        try:
            model = loader.loadModel('phase_3.5/models/gui/battle_gui')
            node = model.find('**/PckMn_Arrow_Up' if direction == 'up' else '**/PckMn_Arrow_DN')
            if node and not node.isEmpty():
                return node
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ refresh
    def refreshAll(self):
        if not self.visible:
            return
        self._applyLayout()
        self._rebuildTracks(preserve_scroll=True)
        self._refreshYou()
        self._refreshTeammates()
        self._refreshCogs()
        self._refreshFire()
        self._refreshConfirm()
        self._rebuildFocus()

    def updateBattleState(self, suits, luredIndices, trappedIndices, toons, localNum):
        if not self.visible:
            return
        self.suits = list(suits or [])
        self.toons = list(toons or [])
        self.lured = list(luredIndices or [])
        self.trapped = list(trappedIndices or [])
        self.localNum = localNum
        if self.locked and self.pending and not self._pendingStillValid():
            self._notify('That action can no longer be used. Pick again.')
            self.locked = False
            self.pending = None
            self._returnToGags()
        self._refreshYou()
        self._refreshTeammates()
        self._refreshCogs()
        if self.targetMode:
            self._showTargets(self.targetMode == 'toon')
        self._refreshConfirm()

    def updateChosenAttacks(self, battleIndices, tracks, levels, targets):
        if not self.visible:
            return
        previous = self.prevTargets
        self.chosen = (list(battleIndices), list(tracks), list(levels), list(targets))
        self._refreshTeammates()
        self._refreshConfirm()
        if self.locked and self._localUndoOpen():
            note = self._comboText()
            if note and note != self._lastComboNote:
                self._lastComboNote = note
                self._notify('The team combination changed.')

    def showState(self, state):
        if not self.visible:
            return
        if state == 'Attack':
            if self.keepSelection:
                self._blockRoundOpen = False
            elif self._blockRoundOpen:
                if self.blockCooldown > 0:
                    self.blockCooldown -= 1
                self._blockRoundOpen = False
            self._refreshBlockButton()
        elif state == 'AttackWait':
            self._blockRoundOpen = True
        if state == 'Attack' and not self.keepSelection and not self.locked:
            self.targetPanel.hide()
            self.targetMode = None
        elif state == 'AttackWait':
            self.targetPanel.hide()
        self.keepSelection = False
        self._refreshConfirm()

    def noteTurnTimer(self, seconds):
        self.turnTime = seconds
        if not self.loaded:
            return
        try:
            whole = int(seconds)
        except (TypeError, ValueError):
            whole = 0
        self.timerLabel['text'] = 'Turn %ss' % whole
        if getattr(self, 'confirmBtn', None) is not None and self.visible:
            self._refreshConfirm()
        if whole <= 5:
            self.timerLabel['text_fg'] = (1, 0.35, 0.3, 1)
            self._startPulse(self.timerLabel)
        elif whole <= 10:
            self.timerLabel['text_fg'] = (1, 0.75, 0.3, 1)
            self._stopPulse()
        else:
            self.timerLabel['text_fg'] = (1, 1, 1, 1)
            self._stopPulse()

    def setRoster(self, ids):
        self.roster = [int(i) for i in (ids or [])]
        if self.visible:
            self._refreshTeammates()

    def setUndoWindow(self, toonId, seconds):
        toonId = int(toonId)
        seconds = float(seconds)
        if seconds <= 0:
            self.undoLeft.pop(toonId, None)
        else:
            self.undoLeft[toonId] = seconds
        if self.undoTask:
            taskMgr.remove(self.undoTask)
            self.undoTask = None
        if self.undoLeft:
            self.undoTask = taskMgr.doMethodLater(0.1, self._tickUndo, 'tta-undo-tick')
        if self.visible:
            self._refreshTeammates()
            self._refreshConfirm()

    def _tickUndo(self, task):
        done = []
        for toonId in list(self.undoLeft):
            self.undoLeft[toonId] = max(0.0, self.undoLeft[toonId] - 0.1)
            if self.undoLeft[toonId] <= 0:
                done.append(toonId)
        for toonId in done:
            self.undoLeft.pop(toonId, None)
        if self.visible:
            self._refreshConfirm()
        if not self.undoLeft:
            return task.done
        return task.again

    # ------------------------------------------------------------------ player + teammates
    def _colorFor(self, avId, fallbackIndex):
        if avId in self.roster:
            return PLAYER_COLORS[self.roster.index(avId) % len(PLAYER_COLORS)]
        return PLAYER_COLORS[fallbackIndex % len(PLAYER_COLORS)]

    def _refreshYou(self):
        av = base.localAvatar
        try:
            hp, mx = av.getHp(), av.getMaxHp()
        except Exception:
            hp, mx = 0, 1
        status = 'Sad' if hp <= 0 else 'OK'
        self.youLabel['text'] = '%s\nLaff %s/%s\n%s' % (av.getName(), hp, mx, status)
        self.youLabel['text_align'] = TextNode.ACenter
        self.youLabel['text_fg'] = (1, 1, 1, 1)
        self.youLabel['text_shadow'] = (0, 0, 0, 1)
        self._overlayYou()

    def _ensureTeammateSlots(self, count):
        if self.teammatePanels and ('headBox' not in self.teammatePanels[0] or 'laff' not in self.teammatePanels[0]):
            for panel in self.teammatePanels:
                panel['frame'].destroy()
            self.teammatePanels = []
        while len(self.teammatePanels) < count:
            i = len(self.teammatePanels)
            frame = DirectFrame(parent=self.teamList, relief=None, pos=(0, 0, 0))
            headBox = DirectButton(
                parent=frame, relief=DGG.FLAT, frameColor=(0.45, 0.48, 0.52, 1),
                frameSize=(-0.055, 0.055, -0.055, 0.055),
                pos=(0, 0, 0.02),
                command=self._pickToonIcon, extraArgs=[i])
            self._rim(headBox, 0.055, 0.055)
            headBox.bind(DGG.ENTER, self._hoverGrow, [headBox])
            name = DirectLabel(parent=frame, relief=None, text='', text_scale=0.028,
                               text_align=TextNode.ACenter, text_fg=(1, 1, 1, 1),
                               text_wordwrap=10, pos=(0, 0, -0.055), textMayChange=1)
            laff = DirectLabel(parent=frame, relief=None, text='', text_scale=0.024,
                               text_align=TextNode.ACenter, text_fg=(0.75, 0.95, 0.8, 1),
                               pos=(0, 0, -0.082), textMayChange=1)
            state = DirectLabel(parent=frame, relief=None, text='Choosing', text_scale=0.024,
                                text_align=TextNode.ACenter, text_fg=(1, 0.85, 0.35, 1),
                                pos=(0, 0, -0.105), textMayChange=1)
            self.teammatePanels.append({
                'frame': frame, 'headBox': headBox, 'name': name, 'laff': laff, 'state': state, 'headFor': None})

    def _refreshTeammates(self):
        shown = [(i, av) for i, av in enumerate(self.toons) if av is not None]
        self._ensureTeammateSlots(len(self.toons))
        gap = 0.32
        order = 0
        for i, panel in enumerate(self.teammatePanels):
            if i >= len(self.toons) or self.toons[i] is None:
                panel['frame'].hide()
                continue
            panel['frame'].show()
            panel['frame'].setPos(self._fieldX(order, len(shown), gap), 0, -0.02)
            order += 1
            av = self.toons[i]
            try:
                panel['name']['text'] = av.getName()
            except Exception:
                panel['name']['text'] = 'Toon'
            try:
                panel['laff']['text'] = 'Laff %s/%s' % (av.getHp(), av.getMaxHp())
            except Exception:
                panel['laff']['text'] = ''
            self._setTeamHead(panel, av)
            gagText, confirmed = self._chosenTextFor(i)
            if confirmed:
                panel['state']['text'] = 'Ready'
                panel['state']['text_fg'] = (0.55, 1, 0.55, 1)
            else:
                panel['state']['text'] = 'Choosing'
                panel['state']['text_fg'] = (1, 0.85, 0.35, 1)
            self._hideChooser(av)

    def _setTeamHead(self, panel, av):
        box = panel.get('headBox')
        if box is None or av is None:
            return
        try:
            key = av.doId
        except Exception:
            key = id(av)
        if panel.get('headFor') == key:
            return
        old = box.getPythonTag('head') if box.hasPythonTag('head') else None
        if old is not None:
            old.removeNode()
        try:
            from toontown.toon import ToonHead
            head = ToonHead.ToonHead()
            head.setupHead(av.style, forGui=1)
            head.fitAndCenterHead(0.09, forGui=1)
            head.reparentTo(box)
            box.setPythonTag('head', head)
            panel['headFor'] = key
        except Exception:
            panel['headFor'] = key

    def _hideChooser(self, av):
        if av is None:
            return
        label = getattr(av, 'ttaChooserLabel', None)
        if label is not None:
            label.hide()

    def _overlayChooser(self, av, status):
        # Name, laff, and status stay on the team bar.
        self._hideChooser(av)

    def _chosenTextFor(self, index):
        ids, tracks, levels, targets = self.chosen
        if index >= len(tracks):
            return ('', False)
        track = tracks[index]
        if track in (BattleBase.NO_ATTACK, BattleBase.UN_ATTACK, None):
            return ('', False)
        if track in (BattleBase.PASS, BattleBase.PASS_ATTACK):
            return ('Pass', True)
        if track == getattr(BattleBase, 'BLOCK', 101):
            return ('Block', True)
        if track in (BattleBase.SOS, BattleBase.NPCSOS, BattleBase.PETSOS):
            return ('SOS', True)
        if track == BattleBase.FIRE:
            return ('Fire', True)
        if 0 <= track < TBG.NUM_GAG_TRACKS:
            level = levels[index] if index < len(levels) else -1
            name = TBG.AvPropStrings[track][level] if level >= 0 else TBG.Tracks[track]
            tgt = targets[index] if index < len(targets) else -1
            suffix = ' (all)' if tgt == -1 else ''
            return (str(name).capitalize() + suffix, True)
        return ('', False)

    # ------------------------------------------------------------------ gags
    def _earnedLevel(self, track):
        """Highest gag index this toon has the experience to use, or -1 if locked."""
        try:
            if not base.localAvatar.hasTrackAccess(track):
                return -1
        except Exception:
            return -1
        try:
            return int(base.localAvatar.experience.getExpLevel(track))
        except Exception:
            pass
        highest = -1
        inv = base.localAvatar.inventory
        for level in range(len(TBG.Levels[track])):
            try:
                if inv.itemIsUsable(track, level):
                    highest = level
            except Exception:
                break
        return highest

    def _inventorySignature(self):
        inv = base.localAvatar.inventory
        rows = []
        for track in range(TBG.NUM_GAG_TRACKS):
            earned = self._earnedLevel(track)
            qty = ()
            if earned >= 0 and inv:
                qty = tuple(inv.numItem(track, level) for level in range(earned + 1))
            rows.append((track, earned, qty))
        return tuple(rows)

    def _rebuildTracks(self, preserve_scroll=False):
        sig = self._inventorySignature()
        if preserve_scroll and sig == self.inventorySig and self.trackRows:
            self._restyleGags()
            return
        if preserve_scroll:
            self._rememberScroll()
        self.inventorySig = sig
        canvas = self.gagRows
        for child in list(canvas.getChildren()):
            child.removeNode()
        self.trackRows = {}
        inv = base.localAvatar.inventory
        row_h = 0.100
        row_hz = 0.048
        shown = 0
        half = max(0.50, getattr(self, '_gagHalf', 0.7) - 0.05)
        # Name boxes share one left edge and one right edge down the column.
        name_w = 0.34
        name_cx = -half + 0.055 + name_w * 0.5
        name_hz = 0.026
        # Icon fills the colored row. The hairline sits inside that row and
        # still wraps the picture and the quantity.
        rim_t = 0.003
        icon_hz = row_hz - rim_t - 0.003
        icon_hx = icon_hz + 0.012
        step = icon_hx * 2 + 0.012
        icon_x0 = name_cx + name_w * 0.5 + 0.02 + icon_hx
        for track in range(TBG.NUM_GAG_TRACKS):
            earned = self._earnedLevel(track)
            color = TBG.TrackColors[track]
            rgb = (color[0], color[1], color[2], 1)
            levels = list(range(earned + 1)) if earned >= 0 else []
            row = DirectFrame(parent=canvas, relief=DGG.FLAT,
                              frameSize=(-half, half, -row_hz, row_hz),
                              frameColor=(rgb[0] * 0.35 + 0.08, rgb[1] * 0.35 + 0.08, rgb[2] * 0.35 + 0.08, 0.95),
                              pos=(0, 0, -shown * row_h))
            DirectFrame(parent=row, relief=DGG.FLAT, frameColor=rgb,
                        frameSize=(-0.008, 0.008, -0.026, 0.026), pos=(-half + 0.03, 0, 0))
            name = str(TBG.Tracks[track]).capitalize()
            nameBox = DirectFrame(parent=row, relief=None, pos=(name_cx, 0, 0))
            DirectLabel(parent=nameBox, relief=None, text=name,
                        text_scale=0.032, text_align=TextNode.ACenter,
                        text_fg=(1, 1, 1, 1), text_font=ToontownGlobals.getInterfaceFont(),
                        pos=(0, 0, -0.01))
            self._rim(nameBox, name_w * 0.5, name_hz, t=rim_t)
            if earned < 0:
                DirectLabel(parent=row, relief=None, text='locked',
                            text_scale=0.036, text_align=TextNode.ACenter,
                            text_fg=(1, 1, 1, 1), text_font=ToontownGlobals.getInterfaceFont(),
                            pos=(0, 0, -0.012))
            self._rim(row, half, row_hz)
            buttons = []
            for n, level in enumerate(levels):
                node = None
                try:
                    node = inv.buttonLookup(track, level)
                except Exception:
                    pass
                qty = inv.numItem(track, level) if inv else 0
                b = DirectButton(
                    parent=row, relief=DGG.FLAT, geom=node,
                    geom_scale=self._gagGeomScale(node, icon_hz * 1.55),
                    geom_pos=(-0.012, 0, 0.004),
                    text=str(qty), text_scale=0.028,
                    text_align=TextNode.ARight,
                    text_pos=(icon_hx - 0.008, -icon_hz + 0.012),
                    text_fg=(1, 1, 1, 1), text_bg=(0, 0, 0, 0.45),
                    frameColor=(0.45, 0.48, 0.52, 0.62),
                    frameSize=(-icon_hx, icon_hx, -icon_hz, icon_hz),
                    pos=(icon_x0 + n * step, 0, 0),
                    command=self._selectGag, extraArgs=[track, level])
                b.setBin('gui-popup', PANEL_SORT)
                self._rim(b, icon_hx, icon_hz, t=rim_t)
                if qty <= 0:
                    b.setColorScale(0.45, 0.45, 0.45, 1)
                b.bind(DGG.ENTER, self._showGagTip, [track, level])
                b.bind(DGG.EXIT, self._clearTooltip)
                b.bind(DGG.B1PRESS, self._beginLongPress, [track, level])
                b.bind(DGG.B1RELEASE, self._endLongPress)
                buttons.append(b)
            self.trackRows[track] = {'frame': row, 'buttons': buttons, 'color': rgb}
            shown += 1
        self._restyleGags()
        print('web: battle gags built tracks=%d buttons=%d' % (
            len(self.trackRows),
            sum(len(row['buttons']) for row in self.trackRows.values())))

    def _gagGeomScale(self, node, target_h):
        """Scale a gag picture so it fills the track row."""
        if node is None:
            return 1.0
        try:
            bounds = node.getTightBounds()
            if bounds:
                low, high = bounds
                height = float(high.getZ() - low.getZ())
                if height > 0.001:
                    return max(0.2, min(2.5, target_h / height))
        except Exception:
            pass
        return 0.72

    def _addPips(self, row, track):
        try:
            level = base.localAvatar.experience.getExpLevel(track)
        except Exception:
            level = 0
        count = len(TBG.Levels[track])
        for i in range(count):
            filled = i <= level
            DirectFrame(parent=row, relief=DGG.FLAT,
                        frameColor=((1, 1, 1, 1) if filled else (1, 1, 1, 0.25)),
                        frameSize=(-0.012, 0.012, -0.012, 0.012),
                        pos=(-0.55 + i * 0.04, 0, 0.055))

    def _restyleGags(self):
        selected = None
        if self.pending and self.pending.get('mode') == 'gag':
            selected = (self.pending['track'], self.pending['level'])
        for track, row in self.trackRows.items():
            for level, button in enumerate(row['buttons']):
                mark = button.getPythonTag('sel') if button.hasPythonTag('sel') else None
                on = selected == (track, level)
                button.setScale(1.0)
                button['frameColor'] = (0.95, 0.78, 0.28, 0.85) if on else (0.45, 0.48, 0.52, 0.62)
                if mark is not None:
                    mark.hide()

    def _rememberScroll(self):
        try:
            self.scrollIndex = self.scroll.verticalScroll['value']
        except Exception:
            pass

    def _restoreScroll(self):
        try:
            self.scroll.verticalScroll['value'] = self.scrollIndex
        except Exception:
            pass

    def _scroll(self, direction):
        try:
            bar = self.scroll.verticalScroll
            value = bar['value'] + (-0.16 if direction > 0 else 0.16)
            bar['value'] = max(0.0, min(1.0, value))
            self.scrollIndex = bar['value']
        except Exception:
            pass

    def _pop(self, node, end_scale=1.0):
        if node is None:
            return
        try:
            Sequence(
                LerpScaleInterval(node, 0.05, end_scale * 0.86),
                LerpScaleInterval(node, 0.09, end_scale * 1.14),
                LerpScaleInterval(node, 0.08, end_scale),
            ).start()
        except Exception:
            pass

    def _selectGag(self, track, level):
        if self.longPressFired:
            self.longPressFired = False
            return
        if self.locked:
            return
        inv = base.localAvatar.inventory
        try:
            usable = inv.numItem(track, level) > 0 and inv.itemIsUsable(track, level)
        except Exception:
            usable = inv.numItem(track, level) > 0
        if not usable:
            return
        self._play('click')
        self._clearTooltip()
        try:
            self._rememberScroll()
        except Exception:
            pass
        group = bool(TBG.isGroup(track, level))
        self.pending = {'mode': 'gag', 'track': track, 'level': level, 'group': group, 'target': None}
        self.tb.track = track
        self.tb.level = level
        self._restyleGags()
        try:
            self._pop(self.trackRows[track]['buttons'][level], 1.08)
        except Exception:
            pass
        if group:
            targets = self._eligible(track, level, toon=(track == TBG.HEAL_TRACK))
            if not targets:
                self.pending['invalid'] = True
            else:
                self.pending['target'] = -1
                self.pending['auto'] = [t[0] for t in targets]
        else:
            targets = self._eligible(track, level, toon=(track == TBG.HEAL_TRACK))
            if len(targets) == 1:
                self.pending['target'] = targets[0][0]
                self.tb.target = targets[0][0]
                if track == TBG.HEAL_TRACK:
                    self._markToonSelection()
                else:
                    self._markCogSelection()
        if getattr(self, 'targetPanel', None) is not None:
            self.targetPanel.hide()
        if getattr(self, 'centerPanel', None) is not None:
            self.centerPanel.show()
        self.targetMode = None
        self._fillGagTip(track, level)
        self._refreshConfirm()

    def _eligible(self, track, level, toon):
        found = []
        if toon:
            for i, av in enumerate(self.toons):
                if av is None or i == self.localNum:
                    continue
                found.append((i, av))
            return found
        for i, av in enumerate(self.suits):
            if av is None:
                continue
            if track == TBG.TRAP_TRACK and (i in self.lured or i in self.trapped):
                continue
            if track == TBG.LURE_TRACK and i in self.lured:
                continue
            found.append((i, av))
        return found

    # ------------------------------------------------------------------ targets
    def _showTargets(self, isToon):
        self.targetMode = 'toon' if isToon else 'cog'
        self.targetPanel.show()
        self.centerPanel.hide()
        for card in self.targetCards:
            card.destroy()
        self.targetCards = []
        track = self.pending['track'] if self.pending else -1
        level = self.pending['level'] if self.pending else -1
        self.targetTitle['text'] = 'Choose a Toon' if isToon else 'Choose a Cog'
        found = self._eligible(track, level, isToon) if self.pending else []
        if not found:
            self._showNoTargets(isToon)
            return
        self.targetEmpty['text'] = ''
        for n, (index, av) in enumerate(found):
            card = self._makeTargetCard(index, av, isToon, n, len(found))
            self.targetCards.append(card)
        self._rebuildFocus()

    def _showNoTargets(self, isToon):
        self.targetMode = 'toon' if isToon else 'cog'
        self.targetPanel.show()
        self.centerPanel.hide()
        for card in self.targetCards:
            card.destroy()
        self.targetCards = []
        self.targetTitle['text'] = 'No valid targets'
        who = 'Toon' if isToon else 'Cog'
        self.targetEmpty['text'] = 'No %s can be chosen for this gag.\nBack keeps your place in the list.' % who
        self._rebuildFocus()

    def _makeTargetCard(self, index, av, isToon, slot, count):
        if isToon:
            name = av.getName()
            hp, mx = av.getHp(), av.getMaxHp()
            extra = ''
        else:
            name = av.getName() if hasattr(av, 'getName') else 'Cog'
            hp = av.getHP()
            mx = getattr(av, 'maxHP', hp)
            bits = []
            if index in self.lured:
                bits.append('Lured')
            if index in self.trapped:
                bits.append('Trapped')
            defense = self._defense(av)
            if defense is not None:
                bits.append('Def %s' % defense)
            extra = ' '.join(bits)
        span = min(0.5, 2.0 / max(1, count))
        card = DirectButton(
            parent=self.targetPanel, relief=DGG.FLAT,
            text=self._targetLabel(av, isToon, hp, mx, extra),
            text_scale=0.04, text_fg=(1, 1, 1, 1),
            frameColor=(0.16, 0.36, 0.58, 1),
            frameSize=(-0.22, 0.22, -0.22, 0.22),
            pos=(-0.75 + slot * span, 0, 0.0),
            command=self._pickTarget, extraArgs=[index])
        card.setBin('gui-popup', TARGET_SORT)
        bar = DirectWaitBar(parent=card, relief=DGG.SUNKEN, range=max(1, mx), value=max(0, hp),
                            frameSize=(-0.18, 0.18, -0.02, 0.02), pos=(0, 0, -0.16),
                            barColor=(0.3, 0.85, 0.4, 1) if isToon else (0.9, 0.3, 0.3, 1))
        bar['state'] = DGG.DISABLED
        return card

    def _targetLabel(self, av, isToon, hp, mx, extra):
        """Name, then 'Department Lvl X', then health numbers below."""
        name = av.getName() if hasattr(av, 'getName') else 'Cog'
        if isToon:
            return '%s\nToon\n%s / %s' % (name, hp, mx)
        level = self._levelOf(av, isToon)
        try:
            dept = av.getStyleDept()
        except Exception:
            dept = None
        if dept:
            try:
                dept = SuitDNA.getDeptFullname(dept)
            except Exception:
                pass
        dept = dept or 'Cog'
        head = '%s Lvl %s' % (dept, level) if level not in (None, '', '???') else str(dept)
        lines = [str(name), str(head), '%s / %s' % (hp, mx)]
        if extra:
            lines.append(extra)
        return '\n'.join(lines)

    def _levelOf(self, av, isToon):
        if isToon:
            return ''
        try:
            return av.getActualLevel()
        except Exception:
            return ''

    def _defense(self, suit):
        try:
            return SuitBattleGlobals.SuitAttributes[suit.dna.name]['def'][suit.getLevel()]
        except Exception:
            return None

    def _pickTarget(self, index):
        if not self.pending or self.locked:
            return
        self._play('click')
        self.pending['target'] = index
        self.tb.target = index
        self.targetPanel.hide()
        self.centerPanel.show()
        self.targetMode = None
        try:
            self._restoreScroll()
        except Exception:
            pass
        self._refreshConfirm()

    def _targetBack(self):
        self._play('back')
        self.targetPanel.hide()
        self.centerPanel.show()
        self.targetMode = None
        if self.pending and self.pending.get('mode') == 'gag' and self.pending.get('target') is None:
            self.pending = None
            self._restyleGags()
        try:
            self._restoreScroll()
        except Exception:
            pass
        self._refreshConfirm()

    def _returnToGags(self):
        self.targetPanel.hide()
        self.runDialog.hide()
        self.centerPanel.show()
        self.targetMode = None
        try:
            self._restoreScroll()
        except Exception:
            pass

    # ------------------------------------------------------------------ actions
    def _pickCogIcon(self, index):
        if self.locked or not self.pending or self.pending.get('mode') != 'gag':
            return
        if self.pending.get('track') == TBG.HEAL_TRACK or self.pending.get('group'):
            return
        self._play('click')
        self.pending['target'] = index
        self.tb.target = index
        self._markCogSelection()
        try:
            self._pop(self.cogCards[index], 1.0)
        except Exception:
            pass
        self._refreshConfirm()

    def _pickToonIcon(self, index):
        if self.locked or not self.pending or self.pending.get('mode') != 'gag':
            return
        if self.pending.get('track') != TBG.HEAL_TRACK or self.pending.get('group'):
            return
        self._play('click')
        self.pending['target'] = index
        self.tb.target = index
        self._markToonSelection()
        self._refreshConfirm()

    def _markCogSelection(self):
        chosen = None
        if self.pending and self.pending.get('mode') == 'gag' and not self.pending.get('group'):
            chosen = self.pending.get('target')
        for i, card in enumerate(getattr(self, 'cogCards', [])):
            card['frameColor'] = (0.95, 0.78, 0.28, 1) if i == chosen else (0.45, 0.48, 0.52, 0.95)

    def _markToonSelection(self):
        chosen = None
        if self.pending and self.pending.get('track') == TBG.HEAL_TRACK:
            chosen = self.pending.get('target')
        for i, panel in enumerate(self.teammatePanels):
            box = panel.get('headBox')
            if box is None:
                continue
            box['frameColor'] = (0.95, 0.78, 0.28, 1) if i == chosen else (0.45, 0.48, 0.52, 1)

    def _readyBlock(self):
        av = base.localAvatar
        try:
            av.ttaBlockActive = True
        except Exception:
            pass
        played = False
        for anim in ('duck', 'cringe', 'jump'):
            try:
                if av.getAnimControl(anim):
                    av.play(anim)
                    played = True
                    break
            except Exception:
                continue
        if not played:
            self._pop(av, 1.0)

    def _overlayYou(self):
        label = getattr(self, 'youLabel', None)
        if label is not None:
            label.hide()

    def _refreshBlockButton(self):
        if len(self.actionButtons) < 3:
            return
        button = self.actionButtons[2]
        left = getattr(self, 'blockCooldown', 0)
        button['text_align'] = TextNode.ACenter
        if left > 0:
            button['text'] = 'Block %s' % left
            button['text_scale'] = 0.04
            button['text_pos'] = (0, -0.012)
            button['frameColor'] = (0.30, 0.32, 0.36, 1)
            button['state'] = DGG.DISABLED
        else:
            button['text'] = 'Block'
            button['text_scale'] = 0.05
            button['text_pos'] = (0, -0.015)
            button['frameColor'] = (0.28, 0.40, 0.54, 1)
            button['state'] = DGG.NORMAL

    def _chooseBlock(self):
        if self._swallowLongPress() or self.locked:
            return
        if getattr(self, 'blockCooldown', 0) > 0:
            self._notify('Block is cooling down.')
            return
        self._play('click')
        self._pop(self.actionButtons[2])
        self.pending = {'mode': 'block'}
        self._readyBlock()
        self._restoreDetails()
        self._refreshConfirm()

    def _choosePass(self):
        if self._swallowLongPress() or self.locked:
            return
        self._play('click')
        self._pop(self.actionButtons[3])
        self.pending = {'mode': 'pass'}
        self._restoreDetails()
        self._refreshConfirm()

    def _askRun(self):
        if self._swallowLongPress() or self.locked:
            return
        self._play('pop')
        self._pop(self.actionButtons[0])
        self.runOpen = True
        self.runDialog.show()

    def _cancelRun(self):
        self._play('back')
        self.runOpen = False
        self.runDialog.hide()

    def _confirmRun(self):
        self._play('click')
        self.runOpen = False
        self.runDialog.hide()
        messenger.send(self.tb.battleEvent, [{'mode': 'Run'}])
        self.conceal()

    def _doFire(self):
        if self._swallowLongPress() or self.locked:
            return
        self._play('click')
        try:
            self.tb.fsm.request('Fire')
            frame = getattr(self.tb.FireCogPanel, 'frame', self.tb.FireCogPanel)
            frame.show()
            frame.setBin('gui-popup', DIALOG_SORT)
        except Exception:
            self._notify('Fire is not available.')

    def _toggleSos(self):
        if self._swallowLongPress() or self.locked:
            return
        self._play('pop')
        self.sosOpen = not self.sosOpen
        if self.sosOpen:
            self._buildSosCards()
            self.sosFrame.show()
        else:
            self.sosFrame.hide()

    def _buildSosCards(self):
        for card in self.sosCards:
            card.destroy()
        self.sosCards = []
        friends = getattr(base.localAvatar, 'NPCFriendsDict', {}) or {}
        y = 0.0
        for npcId, uses in list(friends.items()):
            if not uses:
                continue
            track, level, hp, rarity = NPCToons.getNPCTrackLevelHpRarity(npcId)
            name = NPCToons.getNPCName(npcId)
            try:
                name = str(name)
            except Exception:
                name = 'SOS'
            effect = ''
            acc = ''
            target = ''
            if track is not None and 0 <= track < TBG.NUM_GAG_TRACKS and level is not None and level >= 0:
                effect = str(TBG.AvPropStrings[track][level]).capitalize()
                if track != TRAP:
                    acc = 'Acc %s%%' % TBG.AvPropAccuracy[track][level]
                target = 'All' if TBG.isGroup(track, level) else 'One'
            text = '%s\n%s  %s\n%s  uses %s  %s' % (
                name, effect, target, acc, uses, ('rarity %s' % rarity) if rarity is not None else '')
            card = DirectButton(
                parent=self.sosFrame, relief=DGG.FLAT, text=text, text_scale=0.032,
                text_fg=(1, 1, 1, 1), frameColor=(0.25, 0.3, 0.5, 1),
                frameSize=(-0.40, 0.40, -0.08, 0.08), pos=(0, 0, y),
                command=self._pickSos, extraArgs=[npcId, name])
            card.setBin('gui-popup', PANEL_SORT)
            self.sosCards.append(card)
            y -= 0.18
        more = DirectButton(
            parent=self.sosFrame, relief=DGG.FLAT, text='Friends and pets',
            text_scale=0.035, text_fg=(1, 1, 1, 1), frameColor=(0.3, 0.35, 0.45, 1),
            frameSize=(-0.40, 0.40, -0.05, 0.05), pos=(0, 0, y),
            command=self._openLegacySos)
        more.setBin('gui-popup', PANEL_SORT)
        self.sosCards.append(more)
        if not friends:
            self._notify('No SOS cards in your book. Friends and pets are in the next list.')

    def _pickSos(self, npcId, name):
        if self.locked:
            return
        self._play('click')
        self.pending = {'mode': 'sos', 'id': npcId, 'name': name}
        self._restoreDetails()
        self._refreshConfirm()

    def _openLegacySos(self):
        """Existing friend and pet SOS, unchanged."""
        try:
            canHeal, canTrap, canLure = self.tb.checkHealTrapLure()
            self.tb.fsm.request('SOS')
            frame = getattr(self.tb.SOSPanel, 'frame', self.tb.SOSPanel)
            frame.show()
            frame.setBin('gui-popup', DIALOG_SORT)
        except Exception:
            self._notify('SOS list could not open.')

    def _refreshFire(self):
        slips = 0
        try:
            slips = base.localAvatar.getPinkSlips()
        except Exception:
            slips = 0
        if slips:
            self.fireButton.show()
            self.fireButton['text'] = 'Fire %s' % slips
        else:
            self.fireButton.hide()

    # ------------------------------------------------------------------ confirm
    def _refreshConfirm(self):
        text, combo = self._summary()
        self.confirmText['text'] = text
        self.confirmCombo['text'] = combo
        self.mobileText['text'] = text
        try:
            turn = max(0, int(self.turnTime))
        except (TypeError, ValueError):
            turn = 0
        localId = base.localAvatar.doId
        undoing = self.locked and self.undoLeft.get(localId, 0) > 0
        ready = self._confirmReady()
        label = 'Confirm %ss' % turn
        self.confirmBtn['text'] = label
        self.mobileBtn['text'] = label
        if undoing:
            undo = 'Undo %ss' % turn
            self.confirmBtn.hide()
            self.undoBtn.show()
            self.undoBtn['text'] = undo
            self.mobileBtn.hide()
            self.mobileUndo.show()
            self.mobileUndo['text'] = undo
        else:
            self.undoBtn.hide()
            self.mobileUndo.hide()
            self.confirmBtn.show()
            self.mobileBtn.show()
            if ready:
                self.confirmBtn['state'] = DGG.NORMAL
                self.mobileBtn['state'] = DGG.NORMAL
                self.confirmBtn['frameColor'] = (0.18, 0.58, 0.28, 1)
                self.mobileBtn['frameColor'] = (0.18, 0.58, 0.28, 1)
                self.confirmBtn['text_fg'] = (1, 1, 1, 1)
                self.mobileBtn['text_fg'] = (1, 1, 1, 1)
            else:
                self.confirmBtn['state'] = DGG.DISABLED
                self.mobileBtn['state'] = DGG.DISABLED
                self.confirmBtn['frameColor'] = (0.30, 0.32, 0.36, 1)
                self.mobileBtn['frameColor'] = (0.30, 0.32, 0.36, 1)
                self.confirmBtn['text_fg'] = (0.75, 0.75, 0.75, 1)
                self.mobileBtn['text_fg'] = (0.75, 0.75, 0.75, 1)
        if self.mobile:
            self.mobileConfirm.show()
        else:
            self.mobileConfirm.hide()
        self._publishConflicts()

    def _confirmReady(self):
        """A gag with a target, or a finished Block, Pass, or SOS choice."""
        pending = self.pending
        if not pending or self.locked or pending.get('invalid'):
            return False
        mode = pending.get('mode')
        if mode == 'gag':
            return pending.get('target') is not None
        return mode in ('block', 'pass', 'sos')

    def _summary(self):
        if not self.pending:
            return ('', '')
        mode = self.pending.get('mode')
        if mode == 'gag':
            return (self._gagSummary(), '')
        if mode == 'block':
            return ('Block. A hit still lands, at half damage.', '')
        if mode == 'pass':
            return ('Pass this round.', '')
        if mode == 'sos':
            return ('SOS: %s' % self.pending.get('name', 'card'), '')
        return ('', '')

    def _gagSummary(self):
        track = self.pending['track']
        level = self.pending['level']
        name = str(TBG.AvPropStrings[track][level]).capitalize()
        info = self._previewOne(track, level, self.pending.get('target'), self.pending.get('group'))
        who = self._targetNames(track, self.pending.get('target'), self.pending.get('group'))
        line = '%s   %s' % (name, who)
        extra = []
        if info.get('amount') is not None and info.get('kind') == 'heal':
            extra.append('Heal %s' % info['amount'])
        elif info.get('amount') is not None and info.get('kind') == 'damage':
            extra.append('%s damage' % info['amount'])
        if info.get('accuracy') is not None:
            extra.append('%s%%' % info['accuracy'])
        if extra:
            line = line + '\n' + '   '.join(extra)
        return line

    def _previewOne(self, track, level, target, group):
        exp = 0
        expLevel = 0
        organic = False
        try:
            exp = base.localAvatar.experience.getExp(track)
            expLevel = base.localAvatar.experience.getExpLevel(track)
            organic = bool(base.localAvatar.checkGagBonus(track, level))
        except Exception:
            pass
        amount = None
        kind = 'none'
        if track not in (LURE,):
            try:
                amount = TBG.getAvPropDamage(track, level, exp, organic)
            except Exception:
                amount = None
        if track == HEAL:
            kind = 'heal'
        elif track != LURE:
            kind = 'damage'
        suit_def = 0
        lured_count = 0
        target_count = 1
        all_lured = False
        if group and track != HEAL:
            target_count = max(1, len([s for s in self.suits if s is not None]))
            lured_count = len(self.lured)
            all_lured = target_count > 0 and lured_count >= target_count
            if self.suits:
                defs = [self._defense(s) or 0 for s in self.suits if s is not None]
                suit_def = defs[0] if defs else 0
        elif not group and track != HEAL and target is not None and 0 <= target < len(self.suits):
            suit_def = self._defense(self.suits[target]) or 0
            lured_count = 1 if target in self.lured else 0
            all_lured = bool(lured_count)
            target_count = 1
        if track == HEAL and group:
            target_count = max(1, len(self.toons) - 1)
        organic_lure = None
        if track == LURE and organic:
            try:
                organic_lure = TBG.AvLureBonusAccuracy[level]
            except Exception:
                organic_lure = None
        prop = 0
        try:
            prop = TBG.AvPropAccuracy[track][level]
        except Exception:
            prop = 0
        acc = preview_accuracy(
            prop, expLevel, suit_def, track,
            all_targets_lured=all_lured, lured_count=lured_count, target_count=target_count,
            organic_lure_acc=organic_lure)
        if kind == 'damage' and track == DROP and all_lured:
            amount = 0
        amount = split_amount(amount, track, group, target_count if track == HEAL else 1)
        lured_names = target in self.lured if target is not None else bool(self.lured)
        notes = effect_notes(track, level, group, lured_names, target in self.trapped if target is not None else False, organic)
        return {'amount': amount, 'kind': kind, 'accuracy': acc, 'notes': notes, 'targets': target_count if group else 1}

    def _targetNames(self, track, target, group):
        if group:
            if track == TBG.HEAL_TRACK:
                names = [av.getName() for i, av in enumerate(self.toons) if av is not None and i != self.localNum]
            else:
                names = [av.getName() for av in self.suits if av is not None]
            return 'All: ' + ', '.join(names) if names else 'All'
        if target is None:
            return 'No target yet'
        pool = self.toons if track == TBG.HEAL_TRACK else self.suits
        if 0 <= target < len(pool) and pool[target] is not None:
            return pool[target].getName()
        return 'Target %s' % target

    def _comboText(self):
        entries = []
        ids, tracks, levels, targets = self.chosen
        for i, track in enumerate(tracks):
            if not (0 <= track < TBG.NUM_GAG_TRACKS):
                continue
            level = levels[i] if i < len(levels) else -1
            if level < 0:
                continue
            group = bool(TBG.isGroup(track, level))
            info = self._previewOne(track, level, targets[i] if i < len(targets) else None, group)
            label = str(TBG.Tracks[track]).capitalize()
            entries.append({
                'track': track,
                'amount': info['amount'] or 0,
                'target': None if group else (targets[i] if i < len(targets) else None),
                'group': group,
                'confirmed': True,
                'label': label,
                'targets': info['targets'],
                'lured': (targets[i] in self.lured) if (not group and i < len(targets)) else bool(self.lured),
            })
        if self.pending and self.pending.get('mode') == 'gag' and not self.locked:
            track = self.pending['track']
            level = self.pending['level']
            group = self.pending.get('group')
            info = self._previewOne(track, level, self.pending.get('target'), group)
            entries.append({
                'track': track,
                'amount': info['amount'] or 0,
                'target': None if group else self.pending.get('target'),
                'group': group,
                'confirmed': False,
                'label': str(TBG.AvPropStrings[track][level]).capitalize(),
                'targets': info['targets'],
                'lured': self.pending.get('target') in self.lured if self.pending.get('target') is not None else False,
            })
        summary = team_summary(entries)
        parts = []
        if summary['damage']:
            parts.append('Team damage about %s' % summary['damage'])
        if summary['heal']:
            parts.append('Team heal about %s' % summary['heal'])
        extra = [n for n in summary['notes'] if 'not confirmed' in n][:2]
        return '\n'.join(parts + extra)

    def _publishConflicts(self):
        ids, tracks, levels, targets = self.chosen
        entries = []
        for i, track in enumerate(tracks):
            if 0 <= track < TBG.NUM_GAG_TRACKS:
                entries.append({
                    'track': track,
                    'amount': 0,
                    'target': targets[i] if i < len(targets) else None,
                    'group': TBG.isGroup(track, levels[i]) if i < len(levels) and levels[i] >= 0 else False,
                    'confirmed': True,
                    'label': str(TBG.Tracks[track]).capitalize(),
                    'lured': (targets[i] in self.lured) if i < len(targets) else False,
                })
        if self.pending and self.pending.get('mode') == 'gag':
            entries.append({
                'track': self.pending['track'],
                'amount': 0,
                'target': self.pending.get('target'),
                'group': self.pending.get('group'),
                'confirmed': self.locked,
                'label': 'Your gag',
                'lured': self.pending.get('target') in self.lured if self.pending.get('target') is not None else False,
            })
        live = set()
        for key, text in team_summary(entries)['conflicts']:
            live.add(key)
            if key not in self.shownConflicts:
                self.shownConflicts.add(key)
                self._notify(text)
        self.shownConflicts &= live

    def _confirmPending(self):
        if self.locked or not self.pending:
            return
        mode = self.pending.get('mode')
        if mode == 'gag':
            if self.pending.get('invalid'):
                self._notify('No valid targets for that gag.')
                return
            if self.pending.get('target') is None and not self.pending.get('group'):
                eligible = self._eligible(self.pending['track'], self.pending['level'], self.pending['track'] == TBG.HEAL_TRACK)
                if len(eligible) == 1:
                    self.pending['target'] = eligible[0][0]
                    self.tb.target = eligible[0][0]
                else:
                    self._notify('Choose a target first.')
                    return
            response = {
                'mode': 'ConfirmAttack',
                'track': self.pending['track'],
                'level': self.pending['level'],
                'target': -1 if self.pending.get('group') else self.pending['target'],
            }
        elif mode == 'block':
            if getattr(self, 'blockCooldown', 0) > 0:
                self._notify('Block is cooling down.')
                return
            self._readyBlock()
            self.blockCooldown = 3
            response = {'mode': 'ConfirmAttack', 'track': BattleBase.BLOCK, 'level': 0, 'target': -1}
        elif mode == 'pass':
            response = {'mode': 'ConfirmPass'}
        elif mode == 'sos':
            response = {'mode': 'ConfirmNPCSOS', 'id': self.pending['id']}
        else:
            return
        self._play('click')
        self.locked = True
        messenger.send(self.tb.battleEvent, [response])
        try:
            if self.tb.fsm.getCurrentState().getName() != 'AttackWait':
                self.tb.fsm.request('AttackWait')
        except Exception:
            pass
        self._refreshConfirm()

    def _undo(self):
        if not self._localUndoOpen():
            self._notify('That confirmation is final.')
            return
        if self.pending and self.pending.get('mode') == 'block':
            self.blockCooldown = 0
            self._refreshBlockButton()
        self._play('back')
        self.locked = False
        self.keepSelection = True
        messenger.send(self.tb.battleEvent, [{'mode': 'Undo'}])
        try:
            self.tb.fsm.request('Attack')
        except Exception:
            pass
        self._returnToGags()
        self._restyleGags()
        self._refreshConfirm()

    def _localUndoOpen(self):
        return self.undoLeft.get(base.localAvatar.doId, 0) > 0

    def _pendingStillValid(self):
        if not self.pending or self.pending.get('mode') != 'gag':
            return True
        track, level = self.pending['track'], self.pending['level']
        try:
            if base.localAvatar.inventory.numItem(track, level) <= 0:
                return False
        except Exception:
            return True
        if self.pending.get('group'):
            return bool(self._eligible(track, level, track == TBG.HEAL_TRACK))
        target = self.pending.get('target')
        if target is None:
            return True
        return any(i == target for i, _av in self._eligible(track, level, track == TBG.HEAL_TRACK))

    # ------------------------------------------------------------------ tooltips
    def _showGagTip(self, track, level, event=None):
        row = self.trackRows.get(track)
        picked = (self.pending and self.pending.get('mode') == 'gag'
                  and self.pending.get('track') == track and self.pending.get('level') == level)
        if row and level < len(row['buttons']) and not picked:
            row['buttons'][level]['frameColor'] = (0.78, 0.84, 0.9, 1)
        if _input_method() == 'touch':
            return
        self._fillGagTip(track, level)
        self._placeTip(event)

    def _fillGagTip(self, track, level):
        name = str(TBG.AvPropStrings[track][level]).capitalize()
        info = self._previewOne(track, level, None, bool(TBG.isGroup(track, level)))
        lines = [name, _kind(track)[1]]
        if info['kind'] == 'damage' and info['amount'] is not None:
            lines.append('Damage %s' % info['amount'])
        elif info['kind'] == 'heal' and info['amount'] is not None:
            lines.append('Heal %s' % info['amount'])
        if info['accuracy'] is not None:
            lines.append('Accuracy %s%%' % info['accuracy'])
        lines.extend(info['notes'][:2])
        try:
            lines.append('You have %s' % base.localAvatar.inventory.numItem(track, level))
        except Exception:
            pass
        self._setDetails('\n'.join(lines))

    def _showActionTip(self, name, event=None):
        if _input_method() == 'touch':
            return
        self._setDetails('%s\n%s' % (name, ACTION_TIPS.get(name, '')))

    def _actionPress(self, name, event=None):
        self._endLongPress()
        self.longPressFired = False

        def fire(task, label=name):
            self.longPressFired = True
            self._setDetails('%s\n%s' % (label, ACTION_TIPS.get(label, '')))
            return task.done
        self.longPressTask = taskMgr.doMethodLater(0.45, fire, 'tta-action-longpress')

    def _placeTip(self, event):
        # Details live in the left panel. The old card must not sit on Team.
        tip = getattr(self, 'tip', None)
        if tip is not None:
            tip.hide()

    def _setDetails(self, text):
        label = getattr(self, 'detailLabel', None)
        if label is not None:
            label['text'] = text or ''
        tip = getattr(self, 'tip', None)
        if tip is not None:
            tip.hide()

    def _restoreDetails(self):
        pending = self.pending or {}
        mode = pending.get('mode')
        if mode == 'gag' and pending.get('track') is not None and pending.get('level') is not None:
            self._fillGagTip(pending['track'], pending['level'])
            return
        if mode in ('block', 'pass', 'sos'):
            name = {'block': 'Block', 'pass': 'Pass', 'sos': 'SOS'}.get(mode, '')
            extra = pending.get('name', '') if mode == 'sos' else ACTION_TIPS.get(name, '')
            self._setDetails(('%s\n%s' % (name if mode != 'sos' else 'SOS', extra)).strip())
            return
        self._setDetails('')

    def _clearTooltip(self, event=None):
        tip = getattr(self, 'tip', None)
        if tip is not None:
            tip.hide()
        if self.loaded:
            self._restyleGags()
            self._restoreDetails()

    def _beginLongPress(self, track, level, event=None):
        self._endLongPress()
        self.longPressFired = False

        def fire(task):
            self.longPressFired = True
            self._fillGagTip(track, level)
            return task.done
        self.longPressTask = taskMgr.doMethodLater(0.45, fire, 'tta-battle-longpress')

    def _endLongPress(self, event=None):
        if self.longPressTask:
            taskMgr.remove(self.longPressTask)
            self.longPressTask = None

    # ------------------------------------------------------------------ focus
    def _rebuildFocus(self):
        self.focusItems = []
        if not self.targetPanel.isHidden():
            for card in self.targetCards:
                self.focusItems.append(card)
            self.focusItems.append(self.targetBack)
        else:
            for track in sorted(self.trackRows):
                self.focusItems.extend(self.trackRows[track]['buttons'])
            self.focusItems.extend(self.actionButtons)
            if self.sosOpen:
                self.focusItems.extend(self.sosCards)
            if not self.confirmBtn.isHidden():
                self.focusItems.append(self.confirmBtn)
            if not self.undoBtn.isHidden():
                self.focusItems.append(self.undoBtn)
        if self.focusIndex >= len(self.focusItems):
            self.focusIndex = 0
        self._paintFocus()

    def _paintFocus(self):
        for i, node in enumerate(self.focusItems):
            try:
                node.setScale(1.08 if i == self.focusIndex else 1.0)
            except Exception:
                pass

    def _moveFocus(self, dx, dz):
        if not self.focusItems:
            self._rebuildFocus()
        if not self.focusItems:
            return
        current = self.focusItems[self.focusIndex]
        try:
            cx, cz = current.getPos(self.root)[0], current.getPos(self.root)[2]
        except Exception:
            cx, cz = 0, 0
        best = None
        bestScore = None
        for i, node in enumerate(self.focusItems):
            if i == self.focusIndex:
                continue
            try:
                x, z = node.getPos(self.root)[0], node.getPos(self.root)[2]
            except Exception:
                continue
            vx, vz = x - cx, z - cz
            if dx and ((dx > 0 and vx <= 0.02) or (dx < 0 and vx >= -0.02)):
                continue
            if dz and ((dz > 0 and vz <= 0.02) or (dz < 0 and vz >= -0.02)):
                continue
            score = abs(vx) + abs(vz) * (0.35 if dx else 1)
            if dz:
                score = abs(vz) + abs(vx) * 0.35
            if bestScore is None or score < bestScore:
                bestScore = score
                best = i
        if best is None:
            best = (self.focusIndex + (1 if (dx > 0 or dz < 0) else -1)) % len(self.focusItems)
        self.focusIndex = best
        self._paintFocus()

    def _activateFocus(self):
        if not self.focusItems:
            return
        node = self.focusItems[self.focusIndex]
        try:
            node.commandFunc(None)
        except Exception:
            try:
                node['command'](*node['extraArgs'])
            except Exception:
                pass

    def _cancelFocus(self):
        if self.runOpen:
            self._cancelRun()
        elif self.targetMode:
            self._targetBack()
        elif self.sosOpen:
            self.sosOpen = False
            self.sosFrame.hide()
        elif self.locked:
            self._undo()

    def _startPulse(self, node):
        if self.pulseTask:
            return
        def tick(task, n=node):
            try:
                phase = int(task.time * 4) % 2
                n.setScale(1.08 if phase else 1.0)
            except Exception:
                return task.done
            return task.cont
        self.pulseTask = taskMgr.add(tick, 'tta-timer-pulse')

    def _stopPulse(self):
        if self.pulseTask:
            taskMgr.remove(self.pulseTask)
            self.pulseTask = None
        try:
            self.timerLabel.setScale(1)
        except Exception:
            pass

    def _notify(self, text, seconds=3.2):
        self._clearNotification()
        self.notice['text'] = text
        self.notice.show()
        self._play('pop')
        self.noticeTask = taskMgr.doMethodLater(seconds, self._clearNotification, 'tta-battle-notice')

    def _clearNotification(self, task=None):
        if self.noticeTask:
            taskMgr.remove(self.noticeTask)
            self.noticeTask = None
        if self.loaded:
            self.notice.hide()
        if task is not None:
            return task.done
