"""Modern boss-battle gag-selection UI.

Additive overlay on TownBattle. Legacy panels stay loaded and are only hidden
while this UI is up. Config tta-battle-overhaul=0 never constructs it.

Network traffic uses the existing battle events, plus requestConfirm for the
8-second undo window (server-authoritative). Gag icons are the inventory's
existing artwork. Panel art is generated here and is not a replacement for
those icons.
"""
from panda3d.core import *
from direct.gui.DirectGui import *
from direct.interval.IntervalGlobal import Sequence, LerpScaleInterval, Parallel, Func
from direct.showbase.DirectObject import DirectObject
from direct.directnotify import DirectNotifyGlobal

from toontown.toonbase import ToontownBattleGlobals as TBG
from toontown.toonbase import TTLocalizer, ToontownGlobals
from toontown.battle import BattleBase
from toontown.battle import SuitBattleGlobals
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


def _panel_texture(width, height, fill, border, radius=18):
    """New UI texture. Gag icons are not drawn here."""
    img = PNMImage(width, height)
    img.addAlpha()
    fr, fg, fb, fa = fill
    br, bgc, bb, ba = border
    for y in range(height):
        for x in range(width):
            # Rounded rectangle with a 4px border and a slight vertical gradient.
            dx = min(x, width - 1 - x)
            dy = min(y, height - 1 - y)
            inside = dx >= radius or dy >= radius or (radius - dx) ** 2 + (radius - dy) ** 2 <= radius ** 2
            if not inside:
                img.setAlpha(x, y, 0)
                continue
            edge = dx < radius + 4 and dy < radius + 4 and (radius + 3 - dx) ** 2 + (radius + 3 - dy) ** 2 > (radius - 1) ** 2
            edge = edge or dx < 3 or dy < 3
            shade = 1.0 - 0.12 * (y / float(height))
            if edge:
                img.setXelA(x, y, br, bgc, bb, ba)
            else:
                img.setXelA(x, y, fr * shade, fg * shade, fb * shade, fa)
    tex = Texture('tta-battle-ui')
    tex.load(img)
    tex.setMinfilter(Texture.FTLinear)
    tex.setMagfilter(Texture.FTLinear)
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
        self._buildChrome()
        self._buildLeftPanel()
        self._buildCenterPanel()
        self._buildRightPanel()
        self._buildTargetOverlay()
        self._buildRunDialog()
        self._buildTooltip()
        self._buildNotification()
        self._applyLayout()

    def enter(self):
        self.load()
        self.visible = True
        self.root.show()
        self._wire()
        self.beginRound()
        self.refreshAll()
        print('web: battle overhaul UI entered tracks=%d' % len(self.trackRows))

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
        if not self.toons:
            try:
                self.toons = [base.localAvatar]
            except Exception:
                pass
        if self.loaded:
            self.targetPanel.hide()
            self.runDialog.hide()
            self.sosFrame.hide()
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
    def _tex(self, fill, border):
        tex = _panel_texture(128, 96, fill, border)
        self.textures.append(tex)
        return tex

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
        self.leftPanel = DirectFrame(
            parent=self.root, relief=DGG.FLAT,
            frameColor=(0.10, 0.14, 0.24, 0.92),
            frameSize=(-0.36, 0.36, -0.50, 0.24))
        self.leftPanel.setBin('gui-popup', PANEL_SORT)
        self.teamTitle = DirectLabel(parent=self.leftPanel, relief=None, text='Team',
                                     text_scale=0.045, text_fg=(1, 1, 1, 1), pos=(0, 0, 0.16))
        self.teamToggle = DirectButton(
            parent=self.leftPanel, relief=DGG.FLAT, text='Team', text_scale=0.05,
            text_fg=(1, 1, 1, 1), frameColor=(0.2, 0.4, 0.7, 1),
            frameSize=(-0.28, 0.28, -0.06, 0.06), pos=(0, 0, 0.78),
            command=self._toggleTeam)
        self.teamToggle.hide()
        self.teamList = DirectFrame(parent=self.leftPanel, relief=None)

    def _buildCenterPanel(self):
        self.centerPanel = DirectFrame(
            parent=self.root, relief=DGG.FLAT,
            frameColor=(0.96, 0.97, 1.0, 0.94),
            frameSize=(-0.92, 0.92, -0.36, 0.28))
        self.centerPanel.setBin('gui-popup', PANEL_SORT)
        self.gagTitle = DirectLabel(parent=self.centerPanel, relief=None, text='Gags',
                                    text_scale=0.048, text_fg=(0.12, 0.2, 0.38, 1), pos=(0, 0, 0.22))
        up = self._arrow('up')
        down = self._arrow('down')
        self.scroll = DirectScrolledList(
            parent=self.centerPanel, relief=None,
            incButton_image=(up,) * 3 if up is not None else None,
            decButton_image=(down,) * 3 if down is not None else None,
            incButton_relief=None, decButton_relief=None,
            incButton_scale=(0.55, 1, 0.55), decButton_scale=(0.55, 1, 0.55),
            incButton_pos=(0.82, 0, -0.22), decButton_pos=(0.82, 0, 0.10),
            itemFrame_pos=(-0.84, 0, 0.0),
            itemFrame_frameSize=(0, 1.55, -0.28, 0.16),
            numItemsVisible=3, items=[], forceHeight=0.16, pos=(0, 0, -0.02))
        self.scroll.setBin('gui-popup', PANEL_SORT)

    def _buildRightPanel(self):
        self.rightPanel = DirectFrame(
            parent=self.root, relief=DGG.FLAT,
            frameColor=(0.10, 0.14, 0.24, 0.92),
            frameSize=(-0.40, 0.40, -0.42, 0.36))
        self.rightPanel.setBin('gui-popup', PANEL_SORT)
        self.actionTitle = DirectLabel(parent=self.rightPanel, relief=None, text='Actions',
                                       text_scale=0.045, text_fg=(1, 1, 1, 1), pos=(0, 0, 0.30))
        specs = (('Run', self._askRun), ('SOS', self._toggleSos),
                 ('Block', self._chooseBlock), ('Pass', self._choosePass))
        self.actionButtons = []
        for i, (name, cmd) in enumerate(specs):
            col, row = i % 2, i // 2
            b = DirectButton(
                parent=self.rightPanel, relief=DGG.FLAT, text=name, text_scale=0.055,
                text_fg=(1, 1, 1, 1), frameColor=(0.18, 0.42, 0.72, 1),
                frameSize=(-0.16, 0.16, -0.055, 0.055),
                pos=(-0.18 + col * 0.36, 0, 0.16 - row * 0.15), command=cmd)
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
            parent=self.confirmFrame, relief=DGG.FLAT, text='Confirm Turn',
            text_scale=0.042, text_fg=(1, 1, 1, 1), frameColor=(0.18, 0.58, 0.28, 1),
            frameSize=(-0.28, 0.28, -0.055, 0.055), pos=(0, 0, -0.12),
            command=self._confirmPending)
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
            parent=self.mobileConfirm, relief=DGG.FLAT, text='Confirm Turn',
            text_scale=0.05, text_fg=(1, 1, 1, 1), frameColor=(0.18, 0.58, 0.28, 1),
            frameSize=(-0.35, 0.35, -0.06, 0.06), pos=(0.4, 0, 0.05),
            command=self._confirmPending)
        self.mobileUndo = DirectButton(
            parent=self.mobileConfirm, relief=DGG.FLAT, text='Undo',
            text_scale=0.05, text_fg=(1, 1, 1, 1), frameColor=(0.55, 0.35, 0.15, 1),
            frameSize=(-0.25, 0.25, -0.06, 0.06), pos=(-0.45, 0, 0.05),
            command=self._undo)
        self.mobileConfirm.hide()

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
            frameColor=(1, 0.98, 0.9, 0.96),
            frameSize=(-0.48, 0.48, -0.22, 0.22))
        self.tip.setBin('gui-popup', TIP_SORT)
        self.tipLabel = DirectLabel(
            parent=self.tip, relief=None, text='', text_scale=0.04,
            text_fg=(0.1, 0.1, 0.1, 1), text_align=TextNode.ALeft,
            textMayChange=1, text_wordwrap=26, pos=(-0.48, 0, 0.16))
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
        method = _input_method()
        hints = {
            'mouse': 'Click a gag. Hover for details.',
            'touch': 'Tap a gag. Hold for details.',
            'controller': 'Move with the pad. Confirm to choose. Cancel to go back.',
        }
        self.hintLabel['text'] = hints.get(method, hints['mouse'])
        # Sit the trays on the bottom edge. The upper screen stays the battle.
        self.leftPanel.setPos(-1.42, 0, -0.68)
        self.centerPanel.setPos(0.0, 0, -0.68)
        self.rightPanel.setPos(1.42, 0, -0.62)
        self.targetPanel.setPos(0, 0, -0.48)
        self.runDialog.setPos(0, 0, -0.15)
        self.youLabel.setPos(-1.85, 0, -0.28)
        self.timerLabel.setPos(0, 0, -0.28)
        self.hintLabel.setPos(1.85, 0, -0.28)
        if self.mobile:
            self.leftPanel.setScale(0.85)
            self.centerPanel.setScale(0.9)
            self.rightPanel.setScale(0.85)
            self.confirmFrame.hide()
            self.teamToggle.show()
            if self.teamExpanded:
                self.teamList.show()
            else:
                self.teamList.hide()
        else:
            self.leftPanel.setScale(1)
            self.centerPanel.setScale(1)
            self.rightPanel.setScale(1)
            self.teamToggle.hide()
            self.teamList.show()
            self.mobileConfirm.hide()

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
        if self.targetMode:
            self._showTargets(self.targetMode == 'toon')
        self._refreshConfirm()

    def updateChosenAttacks(self, battleIndices, tracks, levels, targets):
        if not self.visible:
            return
        previous = self.prevTargets
        self.chosen = (list(battleIndices), list(tracks), list(levels), list(targets))
        self._refreshTeammates()
        self._flashChangedTargets(previous)
        self._refreshConfirm()
        if self.locked and self._localUndoOpen():
            note = self._comboText()
            if note and note != self._lastComboNote:
                self._lastComboNote = note
                self._notify('The team combination changed.')

    def showState(self, state):
        if not self.visible:
            return
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
        self.youLabel['text'] = '%s   Laff %s/%s   %s' % (av.getName(), hp, mx, status)

    def _ensureTeammateSlots(self, count):
        while len(self.teammatePanels) < count:
            i = len(self.teammatePanels)
            frame = DirectFrame(parent=self.teamList, relief=DGG.FLAT,
                                frameColor=(0.18, 0.24, 0.38, 1),
                                frameSize=(-0.32, 0.32, -0.075, 0.075),
                                pos=(0, 0, 0.04 - i * 0.17))
            name = DirectLabel(parent=frame, relief=None, text='', text_scale=0.045,
                               text_align=TextNode.ALeft, text_fg=(1, 1, 1, 1),
                               pos=(-0.36, 0, 0.05), textMayChange=1)
            gag = DirectLabel(parent=frame, relief=None, text='', text_scale=0.038,
                              text_align=TextNode.ALeft, text_fg=(0.9, 0.95, 1, 1),
                              pos=(-0.36, 0, -0.02), textMayChange=1)
            state = DirectLabel(parent=frame, relief=None, text='Choosing', text_scale=0.038,
                                text_align=TextNode.ALeft, text_fg=(1, 0.85, 0.35, 1),
                                pos=(-0.36, 0, -0.08), textMayChange=1)
            self.teammatePanels.append({'frame': frame, 'name': name, 'gag': gag, 'state': state})

    def _refreshTeammates(self):
        count = len(self.toons)
        self._ensureTeammateSlots(count)
        for i, panel in enumerate(self.teammatePanels):
            if i >= count or self.toons[i] is None:
                panel['frame'].hide()
                continue
            panel['frame'].show()
            av = self.toons[i]
            try:
                panel['name']['text'] = av.getName()
                avId = av.doId
            except Exception:
                panel['name']['text'] = 'Toon'
                avId = i
            col = self._colorFor(avId, i)
            panel['frame']['frameColor'] = (col[0] * 0.45, col[1] * 0.45, col[2] * 0.45, 1)
            gagText, confirmed = self._chosenTextFor(i)
            panel['gag']['text'] = gagText
            if confirmed:
                panel['state']['text'] = 'Ready'
                panel['state']['text_fg'] = (0.55, 1, 0.55, 1)
            else:
                panel['state']['text'] = 'Choosing'
                panel['state']['text_fg'] = (1, 0.85, 0.35, 1)

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

    def _flashChangedTargets(self, previous):
        ids, tracks, levels, targets = self.chosen
        current = {}
        for i, tgt in enumerate(targets):
            current[i] = tgt
            if previous.get(i) == tgt:
                continue
            if i >= len(self.toons) or self.toons[i] is None:
                continue
            try:
                color = self._colorFor(self.toons[i].doId, i)
            except Exception:
                color = self._colorFor(i, i)
            avatar = None
            if 0 <= tgt < len(self.suits):
                avatar = self.suits[tgt]
            elif 0 <= tgt < len(self.toons):
                avatar = self.toons[tgt]
            self._pulseColor(avatar, color)
        self.prevTargets = current

    def _pulseColor(self, avatar, color):
        if avatar is None:
            return
        try:
            before = avatar.getColorScale()
            avatar.setColorScale(color[0], color[1], color[2], 1)

            def restore(task, av=avatar, prev=before):
                try:
                    av.setColorScale(prev)
                except Exception:
                    pass
                return task.done
            taskMgr.doMethodLater(0.55, restore, 'tta-flash-%s' % id(avatar))
        except Exception:
            pass

    # ------------------------------------------------------------------ gags
    def _inventorySignature(self):
        inv = base.localAvatar.inventory
        rows = []
        for track in range(TBG.NUM_GAG_TRACKS):
            if not base.localAvatar.hasTrackAccess(track):
                continue
            qty = tuple(inv.numItem(track, level) for level in range(len(TBG.Levels[track])))
            rows.append((track, qty))
        return tuple(rows)

    def _rebuildTracks(self, preserve_scroll=False):
        sig = self._inventorySignature()
        if preserve_scroll and sig == self.inventorySig and self.trackRows:
            self._restyleGags()
            return
        if preserve_scroll and self.scroll is not None:
            try:
                self.scrollIndex = self.scroll.getSelectedIndex()
            except Exception:
                pass
        self.inventorySig = sig
        try:
            self.scroll.removeAndDestroyAllItems()
        except Exception:
            pass
        self.trackRows = {}
        inv = base.localAvatar.inventory
        for track in range(TBG.NUM_GAG_TRACKS):
            if not base.localAvatar.hasTrackAccess(track):
                continue
            levels = list(range(len(TBG.Levels[track])))
            color = TBG.TrackColors[track]
            rgb = (color[0], color[1], color[2], 1)
            per = min(0.20, 1.45 / max(1, len(levels)))
            if per < 0.14 and len(levels) > 8:
                per = 0.16
            row = DirectFrame(parent=self.scroll, relief=DGG.FLAT,
                              frameSize=(-0.86, 0.86, -0.09, 0.09),
                              frameColor=(rgb[0] * 0.45, rgb[1] * 0.45, rgb[2] * 0.45, 1))
            kind = _kind(track)[1]
            DirectLabel(parent=row, relief=None,
                        text=str(TBG.Tracks[track]).capitalize(),
                        text_scale=0.042, text_align=TextNode.ALeft,
                        text_fg=(1, 1, 1, 1), pos=(-0.82, 0, 0.045))
            self._addPips(row, track)
            buttons = []
            for n, level in enumerate(levels):
                node = None
                try:
                    node = inv.buttonLookup(track, level)
                except Exception:
                    pass
                qty = inv.numItem(track, level) if inv else 0
                usable = False
                try:
                    usable = qty > 0 and inv.itemIsUsable(track, level)
                except Exception:
                    usable = qty > 0
                b = DirectButton(
                    parent=row, relief=DGG.FLAT, geom=node,
                    geom_scale=0.5 if node is not None else 1.0,
                    text=str(qty), text_scale=0.032, text_pos=(0.055, -0.055),
                    text_fg=(1, 1, 1, 1), text_bg=(0, 0, 0, 0.45),
                    frameColor=(1, 1, 1, 0.15),
                    frameSize=(-0.07, 0.07, -0.07, 0.07),
                    pos=(-0.55 + n * (per + 0.02), 0, -0.02),
                    command=self._selectGag, extraArgs=[track, level])
                b.setBin('gui-popup', PANEL_SORT)
                if not usable:
                    b.setColorScale(0.45, 0.45, 0.45, 1)
                b.bind(DGG.ENTER, self._showGagTip, [track, level])
                b.bind(DGG.EXIT, self._clearTooltip)
                b.bind(DGG.B1PRESS, self._beginLongPress, [track, level])
                b.bind(DGG.B1RELEASE, self._endLongPress)
                buttons.append(b)
            self.trackRows[track] = {'frame': row, 'buttons': buttons, 'color': rgb}
            self.scroll.addItem(row)
        try:
            self.scroll.scrollTo(self.scrollIndex)
        except Exception:
            pass
        self._restyleGags()

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
                button.setScale(1.12 if on else 1.0)
                if on:
                    button['frameColor'] = (1, 0.95, 0.4, 1)
                    if mark is None:
                        mark = DirectLabel(parent=button, relief=None, text='SEL',
                                           text_scale=0.45, text_fg=(0.1, 0.1, 0.1, 1),
                                           pos=(0, 0, 0.55))
                        button.setPythonTag('sel', mark)
                    mark.show()
                else:
                    button['frameColor'] = (1, 1, 1, 0.15)
                    if mark is not None:
                        mark.hide()

    def _scroll(self, direction):
        try:
            if direction > 0:
                self.scroll.scrollBy(-1)
            else:
                self.scroll.scrollBy(1)
            self.scrollIndex = self.scroll.getSelectedIndex()
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
            self.scrollIndex = self.scroll.getSelectedIndex()
        except Exception:
            pass
        group = bool(TBG.isGroup(track, level))
        self.pending = {'mode': 'gag', 'track': track, 'level': level, 'group': group, 'target': None}
        self.tb.track = track
        self.tb.level = level
        self._restyleGags()
        eligible = self._eligible(track, level, toon=(track == TBG.HEAL_TRACK))
        if not group and len(eligible) == 1:
            self.pending['target'] = eligible[0][0]
            self.tb.target = eligible[0][0]
            self._refreshConfirm()
            return
        if group:
            targets = self._eligible(track, level, toon=(track == TBG.HEAL_TRACK))
            if not targets:
                self.pending['invalid'] = True
                self._showNoTargets(track == TBG.HEAL_TRACK)
                self._refreshConfirm()
                return
            self.pending['target'] = -1
            self.pending['auto'] = [t[0] for t in targets]
            self.targetPanel.hide()
            self.targetMode = None
            self._refreshConfirm()
            return
        self._showTargets(track == TBG.HEAL_TRACK)

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
            text='%s\nLevel %s\n%s / %s\n%s' % (
                name, self._levelOf(av, isToon), hp, mx, extra),
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
            self.scroll.scrollTo(self.scrollIndex)
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
            self.scroll.scrollTo(self.scrollIndex)
        except Exception:
            pass
        self._refreshConfirm()

    def _returnToGags(self):
        self.targetPanel.hide()
        self.runDialog.hide()
        self.centerPanel.show()
        self.targetMode = None
        try:
            self.scroll.scrollTo(self.scrollIndex)
        except Exception:
            pass

    # ------------------------------------------------------------------ actions
    def _chooseBlock(self):
        if self._swallowLongPress() or self.locked:
            return
        self._play('click')
        self.pending = {'mode': 'block'}
        self._refreshConfirm()

    def _choosePass(self):
        if self._swallowLongPress() or self.locked:
            return
        self._play('click')
        self.pending = {'mode': 'pass'}
        self._refreshConfirm()

    def _askRun(self):
        if self._swallowLongPress() or self.locked:
            return
        self._play('pop')
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
        localId = base.localAvatar.doId
        undoing = self.locked and self.undoLeft.get(localId, 0) > 0
        if undoing:
            left = int(self.undoLeft.get(localId, 0) + 0.999)
            label = 'Undo %ss' % left
            self.confirmBtn.hide()
            self.undoBtn.show()
            self.undoBtn['text'] = label
            self.mobileBtn.hide()
            self.mobileUndo.show()
            self.mobileUndo['text'] = label
        elif self.pending and not self.locked and not self.pending.get('invalid'):
            self.confirmBtn.show()
            self.undoBtn.hide()
            self.mobileBtn.show()
            self.mobileUndo.hide()
        else:
            self.confirmBtn.hide()
            self.undoBtn.hide()
            self.mobileBtn.hide()
            self.mobileUndo.hide()
        if self.mobile and (self.pending or self.locked):
            self.mobileConfirm.show()
        else:
            self.mobileConfirm.hide()
        self._publishConflicts()

    def _summary(self):
        if not self.pending:
            return ('Pick a gag or an action.', self._comboText())
        mode = self.pending.get('mode')
        if mode == 'gag':
            return (self._gagSummary(), self._comboText())
        if mode == 'block':
            sample = block_damage(10)
            return ('Block. Incoming hits are scaled for this round only. A 10-point hit would be reported as %s after the roll. The Cog still has to hit.' % sample,
                    self._comboText())
        if mode == 'pass':
            return ('Pass this round.', self._comboText())
        if mode == 'sos':
            return ('SOS: %s' % self.pending.get('name', 'card'), self._comboText())
        return ('', '')

    def _gagSummary(self):
        track = self.pending['track']
        level = self.pending['level']
        name = str(TBG.AvPropStrings[track][level]).capitalize()
        info = self._previewOne(track, level, self.pending.get('target'), self.pending.get('group'))
        who = self._targetNames(track, self.pending.get('target'), self.pending.get('group'))
        bits = [name, who]
        if info.get('amount') is not None and info.get('kind') == 'heal':
            bits.append('Heal about %s' % info['amount'])
        elif info.get('amount') is not None and info.get('kind') == 'damage':
            bits.append('About %s damage' % info['amount'])
        if info.get('accuracy') is not None:
            bits.append('Accuracy %s%%' % info['accuracy'])
        if info.get('notes'):
            bits.append(info['notes'][0])
        return '\n'.join(bits)

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
                self._notify('Choose a target first.')
                return
            response = {
                'mode': 'ConfirmAttack',
                'track': self.pending['track'],
                'level': self.pending['level'],
                'target': -1 if self.pending.get('group') else self.pending['target'],
            }
        elif mode == 'block':
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
        self.tipLabel['text'] = '\n'.join(lines)
        self.tip.show()

    def _showActionTip(self, name, event=None):
        if _input_method() == 'touch':
            return
        self.tipLabel['text'] = '%s\n%s' % (name, ACTION_TIPS.get(name, ''))
        self.tip.show()
        self._placeTip(event)

    def _actionPress(self, name, event=None):
        self._endLongPress()
        self.longPressFired = False

        def fire(task, label=name):
            self.longPressFired = True
            self.tipLabel['text'] = '%s\n%s' % (label, ACTION_TIPS.get(label, ''))
            self.tip.show()
            return task.done
        self.longPressTask = taskMgr.doMethodLater(0.45, fire, 'tta-action-longpress')

    def _placeTip(self, event):
        if event is None:
            return
        try:
            self.tip.setPos(min(1.2, event.getMouse()[0] + 0.4) if False else 0.2, 0, 0.2)
        except Exception:
            self.tip.setPos(0.2, 0, 0.2)

    def _clearTooltip(self, event=None):
        if self.loaded:
            self.tip.hide()

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
