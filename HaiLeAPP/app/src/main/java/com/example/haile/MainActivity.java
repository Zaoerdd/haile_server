package com.example.haile;

import android.annotation.SuppressLint;
import android.net.Uri;
import android.os.Bundle;
import android.text.TextUtils;
import android.webkit.HttpAuthHandler;
import android.webkit.JsResult;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebChromeClient;
import android.widget.Toast;

import androidx.activity.OnBackPressedCallback;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

public class MainActivity extends AppCompatActivity {

    private static final String RETRY_SCHEME = "haile://retry";
    private static final long EXIT_INTERVAL_MS = 2000L;

    private WebView myWebView;
    private SwipeRefreshLayout swipeRefreshLayout;
    private String homeUrl;
    private String lastRequestedUrl;
    private long lastBackPressedAt = 0L;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        homeUrl = getString(R.string.web_home_url);
        lastRequestedUrl = homeUrl;

        swipeRefreshLayout = findViewById(R.id.swipe_refresh);
        myWebView = findViewById(R.id.webview);

        setupSwipeRefresh();
        setupWebView();
        setupBackNavigation();
        loadUrl(homeUrl);
    }

    private void setupSwipeRefresh() {
        swipeRefreshLayout.setColorSchemeResources(
                android.R.color.holo_blue_bright,
                android.R.color.holo_green_light,
                android.R.color.holo_orange_light
        );
        swipeRefreshLayout.setOnRefreshListener(this::refreshPage);
        swipeRefreshLayout.setOnChildScrollUpCallback((parent, child) -> myWebView.getScrollY() > 0);
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void setupWebView() {
        WebSettings webSettings = myWebView.getSettings();
        webSettings.setJavaScriptEnabled(true);
        webSettings.setDomStorageEnabled(true);
        webSettings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        webSettings.setLoadWithOverviewMode(true);
        webSettings.setUseWideViewPort(true);
        webSettings.setMediaPlaybackRequiresUserGesture(false);

        myWebView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onJsAlert(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setMessage(message)
                        .setCancelable(false)
                        .setPositiveButton(android.R.string.ok, (dialog, which) -> result.confirm())
                        .setOnCancelListener(dialog -> result.cancel())
                        .show();
                return true;
            }

            @Override
            public boolean onJsConfirm(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setMessage(message)
                        .setCancelable(true)
                        .setPositiveButton(android.R.string.ok, (dialog, which) -> result.confirm())
                        .setNegativeButton(android.R.string.cancel, (dialog, which) -> result.cancel())
                        .setOnCancelListener(dialog -> result.cancel())
                        .show();
                return true;
            }
        });

        myWebView.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedHttpAuthRequest(WebView view, HttpAuthHandler handler, String host, String realm) {
                handler.proceed("admin", "haile");
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri requestedUri = request.getUrl();
                if (RETRY_SCHEME.equals(requestedUri.toString())) {
                    refreshPage();
                    return true;
                }
                return false;
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                super.onPageStarted(view, url, favicon);
                if (!TextUtils.isEmpty(url) && !url.startsWith("data:") && !url.startsWith("about:")) {
                    lastRequestedUrl = url;
                }
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                swipeRefreshLayout.setRefreshing(false);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                super.onReceivedError(view, request, error);
                if (request != null && request.isForMainFrame()) {
                    lastRequestedUrl = normalizeUrl(request.getUrl() != null ? request.getUrl().toString() : null);
                    showErrorPage(view);
                }
            }

            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                super.onReceivedError(view, errorCode, description, failingUrl);
                lastRequestedUrl = normalizeUrl(failingUrl);
                showErrorPage(view);
            }
        });
    }

    private void loadUrl(String url) {
        lastRequestedUrl = normalizeUrl(url);
        myWebView.loadUrl(lastRequestedUrl);
    }

    private void refreshPage() {
        swipeRefreshLayout.setRefreshing(true);
        String currentUrl = myWebView.getUrl();
        loadUrl(currentUrl != null ? currentUrl : lastRequestedUrl);
    }

    private String normalizeUrl(String url) {
        if (TextUtils.isEmpty(url) || url.startsWith("data:") || url.startsWith("about:")) {
            return homeUrl;
        }
        return url;
    }

    private void showErrorPage(WebView view) {
        swipeRefreshLayout.setRefreshing(false);
        String errorHtml = "<html><body style='margin:0;display:flex;justify-content:center;align-items:center;height:100vh;flex-direction:column;font-family:sans-serif;background:#071b31;color:#eaf4ff;'>"
                + "<div style='text-align:center;padding:24px;max-width:320px;'>"
                + "<div style='width:72px;height:72px;margin:0 auto 18px;border-radius:24px;background:linear-gradient(135deg,#0f4d79,#08233f);box-shadow:0 16px 40px rgba(3,20,38,0.35);display:flex;align-items:center;justify-content:center;font-size:32px;font-weight:700;'>HL</div>"
                + "<h2 style='margin:0 0 10px;font-size:22px;'>" + getString(R.string.error_console_title) + "</h2>"
                + "<p style='margin:0 0 22px;color:#a9ddff;line-height:1.6;'>" + getString(R.string.error_console_message) + "</p>"
                + "<button onclick=\"window.location.href='" + RETRY_SCHEME + "'\" style='padding:12px 22px;background:#4fb1ff;color:#08233f;border:none;border-radius:999px;font-size:16px;font-weight:700;'>" + getString(R.string.error_console_retry) + "</button>"
                + "</div></body></html>";
        view.loadDataWithBaseURL(null, errorHtml, "text/html", "UTF-8", null);
        Toast.makeText(this, getString(R.string.toast_server_unreachable), Toast.LENGTH_LONG).show();
    }

    private void setupBackNavigation() {
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (myWebView.canGoBack()) {
                    myWebView.goBack();
                    return;
                }

                long now = System.currentTimeMillis();
                if (now - lastBackPressedAt < EXIT_INTERVAL_MS) {
                    finish();
                    return;
                }

                lastBackPressedAt = now;
                Toast.makeText(MainActivity.this, getString(R.string.back_again_to_exit), Toast.LENGTH_SHORT).show();
            }
        });
    }

    @Override
    protected void onDestroy() {
        if (myWebView != null) {
            myWebView.destroy();
        }
        super.onDestroy();
    }
}
