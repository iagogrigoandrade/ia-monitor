package br.com.monitoria.android;

import android.app.ActionBar;
import android.app.Activity;
import android.app.AlertDialog;
import android.appwidget.AppWidgetManager;
import android.content.ComponentName;
import android.content.DialogInterface;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.MenuItem;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.HttpAuthHandler;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebChromeClient;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.TextView;
import android.widget.Toast;

public final class MainActivity extends Activity {
    private static final int MENU_SETTINGS = 2;
    private static final int MENU_ADD_WIDGET = 3;

    // Ajusta o cabecalho web dentro do app: esconde o botao "Flutuante"
    // (recurso de navegador desktop) e adiciona o menu "3 pontinhos" nativo.
    private static final String HEADER_SCRIPT = "(function(){"
            + "var f=document.getElementById('btnFloating');"
            + "if(f){f.style.display='none';}"
            + "if(document.getElementById('btnAppMenu')||!window.MonitorApp){return;}"
            + "var btns=document.querySelector('header .btns');"
            + "if(!btns){return;}"
            + "var b=document.createElement('button');"
            + "b.id='btnAppMenu';"
            + "b.className='ghost';"
            + "b.textContent='\\u22EE';"
            + "b.title='Menu do aplicativo';"
            + "b.setAttribute('aria-label','Menu do aplicativo');"
            + "b.style.fontSize='18px';"
            + "b.style.lineHeight='1';"
            + "b.style.fontWeight='700';"
            + "b.onclick=function(){MonitorApp.openMenu();};"
            + "btns.appendChild(b);"
            + "})();";

    private WebView webView;
    private LinearLayout errorPanel;
    private TextView errorMessage;
    private View menuAnchor;
    private boolean authenticationDialogVisible;
    private boolean mainFrameLoadFailed;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        ActionBar actionBar = getActionBar();
        if (actionBar != null) {
            actionBar.hide();
        }

        webView = new WebView(this);
        webView.setBackgroundColor(Color.rgb(27, 36, 56));
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.getSettings().setLoadWithOverviewMode(true);
        webView.getSettings().setUseWideViewPort(true);
        webView.getSettings().setSupportMultipleWindows(false);
        webView.setWebChromeClient(new WebChromeClient());
        webView.setWebViewClient(new PanelWebViewClient());
        webView.addJavascriptInterface(new AppBridge(), "MonitorApp");

        FrameLayout root = new FrameLayout(this);
        root.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));
        errorPanel = createErrorPanel();
        root.addView(errorPanel, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));
        menuAnchor = new View(this);
        FrameLayout.LayoutParams anchorParams = new FrameLayout.LayoutParams(1, 1, Gravity.TOP | Gravity.END);
        anchorParams.topMargin = dp(56);
        anchorParams.rightMargin = dp(12);
        root.addView(menuAnchor, anchorParams);
        setContentView(root);

        if (MonitorConfig.load(this).hasServer()) {
            loadPanel();
        } else {
            showServerSettings(true);
        }
    }

    private void showAppMenu() {
        PopupMenu popup = new PopupMenu(this, menuAnchor, Gravity.END);
        popup.getMenu().add(0, MENU_ADD_WIDGET, 1, R.string.action_add_widget);
        popup.getMenu().add(0, MENU_SETTINGS, 2, R.string.action_settings);
        popup.setOnMenuItemClickListener(this::onAppMenuItemSelected);
        popup.show();
    }

    private boolean onAppMenuItemSelected(MenuItem item) {
        if (item.getItemId() == MENU_SETTINGS) {
            showServerSettings(false);
            return true;
        }
        if (item.getItemId() == MENU_ADD_WIDGET) {
            requestWidgetPin();
            return true;
        }
        return false;
    }

    private final class AppBridge {
        @JavascriptInterface
        public void openMenu() {
            runOnUiThread(MainActivity.this::showAppMenu);
        }
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        webView.destroy();
        super.onDestroy();
    }

    private void loadPanel() {
        MonitorConfig.Config config = MonitorConfig.load(this);
        if (!config.hasServer()) {
            showServerSettings(true);
            return;
        }
        hidePanelError();
        webView.loadUrl(config.serverUrl + "/");
    }

    private void showServerSettings(boolean required) {
        MonitorConfig.Config current = MonitorConfig.load(this);
        int padding = dp(20);
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(padding, 0, padding, 0);

        EditText url = new EditText(this);
        url.setHint(R.string.server_url_hint);
        url.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        url.setSingleLine(true);
        url.setText(current.serverUrl);
        form.addView(url);

        EditText username = new EditText(this);
        username.setHint(R.string.username_hint);
        username.setInputType(InputType.TYPE_CLASS_TEXT);
        username.setSingleLine(true);
        username.setText(current.username);
        form.addView(username);

        EditText password = new EditText(this);
        password.setHint(current.password.isEmpty()
                ? getString(R.string.password_hint)
                : getString(R.string.password_keep_hint));
        password.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        password.setSingleLine(true);
        form.addView(password);

        CheckBox clearPassword = new CheckBox(this);
        clearPassword.setText(R.string.clear_saved_password);
        clearPassword.setVisibility(current.password.isEmpty() ? CheckBox.GONE : CheckBox.VISIBLE);
        form.addView(clearPassword);

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle(R.string.server_settings_title)
                .setMessage(R.string.server_settings_message)
                .setView(form)
                .setNegativeButton(android.R.string.cancel, null)
                .setPositiveButton(R.string.save, null)
                .create();
        dialog.setCanceledOnTouchOutside(!required);
        dialog.setOnShowListener(ignored -> dialog.getButton(DialogInterface.BUTTON_POSITIVE)
                .setOnClickListener(view -> {
                    String updatedPassword = password.getText().toString();
                    if (updatedPassword.isEmpty() && !clearPassword.isChecked()) {
                        updatedPassword = current.password;
                    }
                    try {
                        MonitorConfig.save(
                                this,
                                url.getText().toString(),
                                username.getText().toString(),
                                updatedPassword
                        );
                    } catch (IllegalArgumentException exception) {
                        url.setError(exception.getMessage());
                        return;
                    }
                    dialog.dismiss();
                    loadPanel();
                    MonitorWidgetProvider.requestUpdate(this);
                }));
        dialog.show();
    }

    private void requestWidgetPin() {
        AppWidgetManager manager = getSystemService(AppWidgetManager.class);
        ComponentName provider = new ComponentName(this, MonitorWidgetProvider.class);
        if (manager != null && manager.isRequestPinAppWidgetSupported()) {
            manager.requestPinAppWidget(provider, null, null);
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle(R.string.widget_help_title)
                .setMessage(R.string.widget_help_message)
                .setPositiveButton(android.R.string.ok, null)
                .show();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private LinearLayout createErrorPanel() {
        int padding = dp(28);
        LinearLayout panel = new LinearLayout(this);
        panel.setBackgroundColor(Color.rgb(27, 36, 56));
        panel.setGravity(Gravity.CENTER);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(padding, padding, padding, padding);
        panel.setVisibility(View.GONE);

        TextView title = new TextView(this);
        title.setText(R.string.panel_error_title);
        title.setTextColor(Color.WHITE);
        title.setTextSize(20);
        title.setGravity(Gravity.CENTER);
        panel.addView(title, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        errorMessage = new TextView(this);
        errorMessage.setTextColor(Color.rgb(203, 213, 225));
        errorMessage.setTextSize(14);
        errorMessage.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams messageParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        messageParams.topMargin = dp(12);
        panel.addView(errorMessage, messageParams);

        Button retry = new Button(this);
        retry.setText(R.string.panel_error_retry);
        retry.setOnClickListener(view -> loadPanel());
        LinearLayout.LayoutParams retryParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        retryParams.topMargin = dp(22);
        panel.addView(retry, retryParams);

        Button settings = new Button(this);
        settings.setText(R.string.panel_error_settings);
        settings.setOnClickListener(view -> showServerSettings(false));
        panel.addView(settings, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        return panel;
    }

    private void showPanelError(CharSequence message) {
        errorMessage.setText(message);
        webView.setVisibility(View.INVISIBLE);
        errorPanel.setVisibility(View.VISIBLE);
    }

    private void hidePanelError() {
        webView.setVisibility(View.VISIBLE);
        errorPanel.setVisibility(View.GONE);
    }

    private final class PanelWebViewClient extends WebViewClient {
        @Override
        public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
            mainFrameLoadFailed = false;
            hidePanelError();
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            if (!mainFrameLoadFailed) {
                hidePanelError();
            }
            view.evaluateJavascript(HEADER_SCRIPT, null);
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            if (!request.isForMainFrame()) {
                return false;
            }
            String scheme = request.getUrl().getScheme();
            if ("http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme)) {
                // Mantem redirecionamentos (por exemplo, HTTP para HTTPS) no WebView.
                return false;
            }
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, request.getUrl()));
                return true;
            } catch (Exception ignored) {
                showPanelError(getString(R.string.panel_error_unsupported_link));
                return true;
            }
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            super.onReceivedError(view, request, error);
            if (request.isForMainFrame()) {
                mainFrameLoadFailed = true;
                showPanelError(getString(R.string.panel_error_network, error.getDescription()));
            }
        }

        @Override
        public void onReceivedHttpAuthRequest(WebView view, HttpAuthHandler handler, String host, String realm) {
            MonitorConfig.Config config = MonitorConfig.load(MainActivity.this);
            if (!config.username.isEmpty() && !config.password.isEmpty()) {
                handler.proceed(config.username, config.password);
                return;
            }
            handler.cancel();
            if (!authenticationDialogVisible) {
                authenticationDialogVisible = true;
                Toast.makeText(MainActivity.this, R.string.authentication_needed, Toast.LENGTH_LONG).show();
                view.post(() -> {
                    showServerSettings(false);
                    authenticationDialogVisible = false;
                });
            }
        }
    }
}
