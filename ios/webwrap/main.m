// Toontown: Anywhere, web wrapper (test build): nothing but the website's own WASM build in a full-screen WKWebView.
// It loads the same page the Home Screen app loads and the page fetches and caches the game files exactly as the PWA does.
// Nothing about the game lives here, so a fix to the website is a fix to this app.
#import <UIKit/UIKit.h>
#import <WebKit/WebKit.h>
#import <AVFoundation/AVFoundation.h>

#ifndef PAGE_URL
#define PAGE_URL @"https://toontown.puffsco.com/play/?install=0"      // install=0: no "Add to Home Screen" card, this already is an app
#endif

@interface WebVC : UIViewController <WKNavigationDelegate, WKUIDelegate>
@property (strong, nonatomic) WKWebView *web;
@property (strong, nonatomic) UILabel *note;
@property (assign, nonatomic) int restarts;
@end

@implementation WebVC

- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = [UIColor colorWithRed:0.35 green:0.62 blue:0.95 alpha:1];
    WKWebViewConfiguration *cfg = [[WKWebViewConfiguration alloc] init];
    cfg.allowsInlineMediaPlayback = YES;
    cfg.mediaTypesRequiringUserActionForPlayback = WKAudiovisualMediaTypeNone;
    cfg.applicationNameForUserAgent = @"TTAWebWrap/1";
    cfg.websiteDataStore = [WKWebsiteDataStore defaultDataStore];       // persistent: the page's file cache survives restarts
    self.web = [[WKWebView alloc] initWithFrame:self.view.bounds configuration:cfg];
    self.web.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    self.web.navigationDelegate = self;
    self.web.UIDelegate = self;
    self.web.opaque = NO;
    self.web.backgroundColor = self.view.backgroundColor;
    self.web.scrollView.backgroundColor = self.view.backgroundColor;
    self.web.scrollView.contentInsetAdjustmentBehavior = UIScrollViewContentInsetAdjustmentNever;      // the page sizes itself with the safe-area values
    self.web.scrollView.scrollEnabled = NO;
    self.web.scrollView.bounces = NO;
    if (@available(iOS 16.4, *)) self.web.inspectable = YES;           // Safari's Develop menu can attach
    [self.view addSubview:self.web];

    self.note = [[UILabel alloc] init];
    self.note.font = [UIFont boldSystemFontOfSize:14];
    self.note.textColor = [UIColor whiteColor];
    self.note.backgroundColor = [UIColor colorWithWhite:0 alpha:0.7];
    self.note.textAlignment = NSTextAlignmentCenter;
    self.note.layer.cornerRadius = 8; self.note.clipsToBounds = YES;
    self.note.hidden = YES;
    [self.view addSubview:self.note];

    [[UIApplication sharedApplication] setIdleTimerDisabled:YES];
    [self load];
}

- (void)load {
    [self.web loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:PAGE_URL] cachePolicy:NSURLRequestUseProtocolCachePolicy timeoutInterval:60]];
}

- (void)showNote:(NSString *)t {
    self.note.text = [NSString stringWithFormat:@"  %@  ", t];
    [self.note sizeToFit];
    CGFloat w = MIN(self.view.bounds.size.width - 40, self.note.bounds.size.width + 20), h = 34;
    self.note.frame = CGRectMake((self.view.bounds.size.width - w) / 2, self.view.safeAreaInsets.top + 10, w, h);
    self.note.hidden = NO;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 6 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{ self.note.hidden = YES; });
}

// iOS ended the page's process (almost always memory): the thing this test is after. Say so, then start again.
- (void)webViewWebContentProcessDidTerminate:(WKWebView *)webView {
    self.restarts++;
    [self showNote:[NSString stringWithFormat:@"iOS stopped the game page (memory?), restart #%d", self.restarts]];
    [self load];
}

- (void)webView:(WKWebView *)webView didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    [self showNote:@"No connection, retrying..."];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 4 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{ [self load]; });
}

- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)action decisionHandler:(void (^)(WKNavigationActionPolicy))handler {
    NSURL *u = action.request.URL;
    BOOL ours = [u.host hasSuffix:@"puffsco.com"] || [u.scheme isEqualToString:@"about"] || [u.scheme isEqualToString:@"blob"] || [u.scheme isEqualToString:@"data"];
    if (action.navigationType == WKNavigationTypeLinkActivated && !ours) {          // a link to another site opens in Safari
        [[UIApplication sharedApplication] openURL:u options:@{} completionHandler:nil];
        handler(WKNavigationActionPolicyCancel);
        return;
    }
    handler(WKNavigationActionPolicyAllow);
}

- (WKWebView *)webView:(WKWebView *)webView createWebViewWithConfiguration:(WKWebViewConfiguration *)c forNavigationAction:(WKNavigationAction *)a windowFeatures:(WKWindowFeatures *)f {
    if (a.request.URL) [[UIApplication sharedApplication] openURL:a.request.URL options:@{} completionHandler:nil];
    return nil;
}

// the page's alert() / confirm() (a web view shows nothing unless the app does)
- (void)webView:(WKWebView *)w runJavaScriptAlertPanelWithMessage:(NSString *)m initiatedByFrame:(WKFrameInfo *)fr completionHandler:(void (^)(void))done {
    UIAlertController *a = [UIAlertController alertControllerWithTitle:nil message:m preferredStyle:UIAlertControllerStyleAlert];
    [a addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:^(UIAlertAction *x) { done(); }]];
    [self presentViewController:a animated:YES completion:nil];
}
- (void)webView:(WKWebView *)w runJavaScriptConfirmPanelWithMessage:(NSString *)m initiatedByFrame:(WKFrameInfo *)fr completionHandler:(void (^)(BOOL))done {
    UIAlertController *a = [UIAlertController alertControllerWithTitle:nil message:m preferredStyle:UIAlertControllerStyleAlert];
    [a addAction:[UIAlertAction actionWithTitle:@"Cancel" style:UIAlertActionStyleCancel handler:^(UIAlertAction *x) { done(NO); }]];
    [a addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:^(UIAlertAction *x) { done(YES); }]];
    [self presentViewController:a animated:YES completion:nil];
}

- (BOOL)prefersStatusBarHidden { return YES; }
- (BOOL)prefersHomeIndicatorAutoHidden { return YES; }
- (UIRectEdge)preferredScreenEdgesDeferringSystemGestures { return UIRectEdgeAll; }
- (UIInterfaceOrientationMask)supportedInterfaceOrientations { return UIInterfaceOrientationMaskLandscape; }

@end

@interface AppDelegate : UIResponder <UIApplicationDelegate>
@property (strong, nonatomic) UIWindow *window;
@end

@implementation AppDelegate
- (BOOL)application:(UIApplication *)app didFinishLaunchingWithOptions:(NSDictionary *)o {
    NSError *e = nil;
    [[AVAudioSession sharedInstance] setCategory:AVAudioSessionCategoryPlayback error:&e];      // sound plays with the silent switch on, as in the native app
    [[AVAudioSession sharedInstance] setActive:YES error:&e];
    self.window = [[UIWindow alloc] initWithFrame:[UIScreen mainScreen].bounds];
    self.window.rootViewController = [[WebVC alloc] init];
    [self.window makeKeyAndVisible];
    return YES;
}
@end

int main(int argc, char *argv[]) {
    @autoreleasepool { return UIApplicationMain(argc, argv, nil, NSStringFromClass([AppDelegate class])); }
}
