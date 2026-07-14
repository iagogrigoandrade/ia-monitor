package br.com.monitoria.android;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;
import android.text.TextUtils;

import androidx.security.crypto.EncryptedSharedPreferences;
import androidx.security.crypto.MasterKey;

import java.io.IOException;
import java.security.GeneralSecurityException;

final class MonitorConfig {
    private static final String SETTINGS_FILE = "monitor_settings";
    private static final String SECURE_FILE = "monitor_secure_settings";
    private static final String KEY_URL = "server_url";
    private static final String KEY_USER = "server_user";
    private static final String KEY_PASSWORD = "server_password";

    private MonitorConfig() {
    }

    static Config load(Context context) {
        SharedPreferences settings = context.getSharedPreferences(SETTINGS_FILE, Context.MODE_PRIVATE);
        return new Config(
                settings.getString(KEY_URL, ""),
                settings.getString(KEY_USER, ""),
                securePreferences(context).getString(KEY_PASSWORD, "")
        );
    }

    static void save(Context context, String serverUrl, String username, String password) {
        context.getSharedPreferences(SETTINGS_FILE, Context.MODE_PRIVATE)
                .edit()
                .putString(KEY_URL, normalizeUrl(serverUrl))
                .putString(KEY_USER, username == null ? "" : username.trim())
                .apply();
        securePreferences(context).edit()
                .putString(KEY_PASSWORD, password == null ? "" : password)
                .apply();
    }

    static String normalizeUrl(String value) {
        String url = value == null ? "" : value.trim();
        if (url.isEmpty()) {
            throw new IllegalArgumentException("Informe a URL do painel.");
        }

        Uri parsed = Uri.parse(url);
        String scheme = parsed.getScheme();
        if ((!("http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme)))
                || TextUtils.isEmpty(parsed.getHost())
                || parsed.getQuery() != null
                || parsed.getFragment() != null) {
            throw new IllegalArgumentException("Use uma URL HTTP ou HTTPS, sem parametros.");
        }

        while (url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }
        return url;
    }

    static String statusUrl(Context context, boolean force) {
        Config config = load(context);
        if (config.serverUrl.isEmpty()) {
            return "";
        }
        return config.serverUrl + "/api/status" + (force ? "?force=1" : "");
    }

    private static SharedPreferences securePreferences(Context context) {
        try {
            MasterKey masterKey = new MasterKey.Builder(context)
                    .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                    .build();
            return EncryptedSharedPreferences.create(
                    context,
                    SECURE_FILE,
                    masterKey,
                    EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                    EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
            );
        } catch (GeneralSecurityException | IOException exception) {
            throw new IllegalStateException("Nao foi possivel abrir as configuracoes seguras.", exception);
        }
    }

    static final class Config {
        final String serverUrl;
        final String username;
        final String password;

        Config(String serverUrl, String username, String password) {
            this.serverUrl = serverUrl == null ? "" : serverUrl;
            this.username = username == null ? "" : username;
            this.password = password == null ? "" : password;
        }

        boolean hasServer() {
            return !serverUrl.isEmpty();
        }
    }
}
