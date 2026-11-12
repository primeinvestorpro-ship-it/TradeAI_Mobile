package com.tradeai.options;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.View;
import android.webkit.ConsoleMessage;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity {

    private static final String PREFS_NAME = "TradeAIPrefs";
    private static final String KEY_SERVER_URL = "server_url";
    private static final String DEFAULT_SERVER_URL = "http://192.168.1.43:5050";

    private WebView webView;
    private ProgressBar progressBar;
    private LinearLayout layoutError;
    private TextView txtCurrentHost;
    private TextView txtErrorMsg;
    private SharedPreferences prefs;
    private long lastBackPressTime = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);

        webView = findViewById(R.id.mainWebView);
        progressBar = findViewById(R.id.webProgressBar);
        layoutError = findViewById(R.id.layoutError);
        txtCurrentHost = findViewById(R.id.txtCurrentHost);
        txtErrorMsg = findViewById(R.id.txtErrorMsg);

        Button btnConfigServer = findViewById(R.id.btnConfigServer);
        Button btnRetry = findViewById(R.id.btnRetry);
        Button btnChangeIp = findViewById(R.id.btnChangeIp);

        btnConfigServer.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showServerConfigDialog();
            }
        });

        btnRetry.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                loadDashboard();
            }
        });

        btnChangeIp.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showServerConfigDialog();
            }
        });

        setupWebView();
        loadDashboard();
    }

    private void setupWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setSupportZoom(true);
        settings.setBuiltInZoomControls(true);
        settings.setDisplayZoomControls(false);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                super.onPageStarted(view, url, favicon);
                progressBar.setVisibility(View.VISIBLE);
                layoutError.setVisibility(View.GONE);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                progressBar.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    progressBar.setVisibility(View.GONE);
                    layoutError.setVisibility(View.VISIBLE);
                    String currentUrl = getServerUrl();
                    txtErrorMsg.setText("Could not reach " + currentUrl + "\n\nError: " + error.getDescription()
                            + "\n\nPlease make sure TradeAI is running on your PC and both devices are on the same Wi-Fi network.");
                }
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                // Keep navigation (including Kite login redirects) inside the app's WebView
                view.loadUrl(request.getUrl().toString());
                return true;
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                if (newProgress < 100) {
                    progressBar.setVisibility(View.VISIBLE);
                    progressBar.setProgress(newProgress);
                } else {
                    progressBar.setVisibility(View.GONE);
                }
            }

            @Override
            public boolean onConsoleMessage(ConsoleMessage cm) {
                return super.onConsoleMessage(cm);
            }
        });
    }

    private String getServerUrl() {
        return prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL);
    }

    private void setServerUrl(String url) {
        String cleanUrl = url.trim();
        if (!cleanUrl.startsWith("http://") && !cleanUrl.startsWith("https://")) {
            cleanUrl = "http://" + cleanUrl;
        }
        if (cleanUrl.endsWith("/")) {
            cleanUrl = cleanUrl.substring(0, cleanUrl.length() - 1);
        }
        prefs.edit().putString(KEY_SERVER_URL, cleanUrl).apply();
    }

    private void loadDashboard() {
        String url = getServerUrl();
        try {
            java.net.URI uri = new java.net.URI(url);
            String hostInfo = uri.getHost() + (uri.getPort() > 0 ? ":" + uri.getPort() : "");
            txtCurrentHost.setText(hostInfo);
        } catch (Exception e) {
            txtCurrentHost.setText(url);
        }
        layoutError.setVisibility(View.GONE);
        webView.loadUrl(url);
    }

    private void showServerConfigDialog() {
        AlertDialog.Builder builder = new AlertDialog.Builder(this);
        builder.setTitle("Configure TradeAI Server");

        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(48, 24, 48, 24);

        TextView label = new TextView(this);
        label.setText("Enter the IP address or URL of the PC running TradeAI:\n(e.g. http://192.168.1.43:5050)");
        label.setPadding(0, 0, 0, 16);
        layout.addView(label);

        final EditText input = new EditText(this);
        input.setText(getServerUrl());
        input.setSelectAllOnFocus(true);
        layout.addView(input);

        LinearLayout btnRow = new LinearLayout(this);
        btnRow.setOrientation(LinearLayout.HORIZONTAL);
        btnRow.setPadding(0, 16, 0, 0);

        Button btnRender = new Button(this);
        btnRender.setText("☁ Render");
        btnRender.setTextSize(11);
        btnRender.setLayoutParams(new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.0f));
        btnRender.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                input.setText("https://tradeai-mobile.onrender.com");
            }
        });
        btnRow.addView(btnRender);

        Button btnLocal = new Button(this);
        btnLocal.setText("💻 Wi-Fi");
        btnLocal.setTextSize(11);
        btnLocal.setLayoutParams(new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.0f));
        btnLocal.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                input.setText("http://192.168.1.43:5050");
            }
        });
        btnRow.addView(btnLocal);

        layout.addView(btnRow);

        builder.setView(layout);

        builder.setPositiveButton("Connect", new DialogInterface.OnClickListener() {
            @Override
            public void onClick(DialogInterface dialog, int which) {
                String newUrl = input.getText().toString();
                if (!newUrl.isEmpty()) {
                    setServerUrl(newUrl);
                    Toast.makeText(MainActivity.this, "Connecting to " + getServerUrl(), Toast.LENGTH_SHORT).show();
                    loadDashboard();
                }
            }
        });

        builder.setNeutralButton("Local PC IP", new DialogInterface.OnClickListener() {
            @Override
            public void onClick(DialogInterface dialog, int which) {
                setServerUrl("http://192.168.1.43:5050");
                loadDashboard();
            }
        });

        builder.setNegativeButton("Cancel", null);
        builder.show();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (webView.canGoBack()) {
                webView.goBack();
                return true;
            }
            if (System.currentTimeMillis() - lastBackPressTime < 2000) {
                finish();
            } else {
                lastBackPressTime = System.currentTimeMillis();
                Toast.makeText(this, "Press back again to exit TradeAI", Toast.LENGTH_SHORT).show();
            }
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }
}
