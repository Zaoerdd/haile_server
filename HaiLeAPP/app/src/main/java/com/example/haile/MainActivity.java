package com.example.haile;

import android.annotation.SuppressLint;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.HttpAuthHandler;
import android.webkit.JsResult;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import androidx.activity.OnBackPressedCallback;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

public class MainActivity extends AppCompatActivity {

    private static final String HOME_URL = "http://8.135.10.38:8080";
    private static final String RETRY_SCHEME = "haile://retry";
    private static final long EXIT_INTERVAL_MS = 2000L;

    private WebView myWebView;
    private SwipeRefreshLayout swipeRefreshLayout;
    private String lastRequestedUrl = HOME_URL;
    private long lastBackPressedAt = 0L;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        swipeRefreshLayout = findViewById(R.id.swipe_refresh);
        myWebView = findViewById(R.id.webview);

        setupSwipeRefresh();
        setupWebView();
        setupBackNavigation();
        loadUrl(HOME_URL);
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

    private void setupWebView() {
        WebSettings webSettings = myWebView.getSettings();
        webSettings.setJavaScriptEnabled(true);
        webSettings.setDomStorageEnabled(true);
        webSettings.setCacheMode(WebSettings.LOAD_NO_CACHE);
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
                if (url != null && !url.startsWith("data:") && !url.startsWith("about:")) {
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
                if (request.isForMainFrame()) {
                    lastRequestedUrl = normalizeUrl(request.getUrl().toString());
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
        if (url == null || url.isBlank() || url.startsWith("data:") || url.startsWith("about:")) {
            return HOME_URL;
        }
        return url;
    }

    private void showErrorPage(WebView view) {
        swipeRefreshLayout.setRefreshing(false);
        String errorHtml = "<html><body style='margin:0;display:flex;justify-content:center;align-items:center;height:100vh;flex-direction:column;font-family:sans-serif;background:#f7f8fa;color:#1f2937;'>"
                + "<div style='text-align:center;padding:24px;'>"
                + "<div style='font-size:48px;'>!</div>"
                + "<h2 style='margin:16px 0 8px;'>Unable to reach the console</h2>"
                + "<p style='margin:0 0 20px;color:#6b7280;'>The server may be offline, or the network connection is unstable.</p>"
                + "<button onclick=\"window.location.href='" + RETRY_SCHEME + "'\" style='padding:10px 20px;background:#2196F3;color:white;border:none;border-radius:999px;font-size:16px;'>Retry</button>"
                + "</div></body></html>";
        view.loadDataWithBaseURL(null, errorHtml, "text/html", "UTF-8", null);
        Toast.makeText(this, "Unable to reach the server. Please check the network and try again.", Toast.LENGTH_LONG).show();
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
                Toast.makeText(MainActivity.this, "再按一次退出", Toast.LENGTH_SHORT).show();
            }
        });
    }
}
