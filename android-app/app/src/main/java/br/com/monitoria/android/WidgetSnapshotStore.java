package br.com.monitoria.android;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

final class WidgetSnapshotStore {
    private static final String FILE_NAME = "widget_snapshot";
    private static final String KEY_RESPONSE = "response";
    private static final String KEY_STATUS = "status";

    private WidgetSnapshotStore() {
    }

    static Snapshot load(Context context) {
        SharedPreferences preferences = context.getSharedPreferences(FILE_NAME, Context.MODE_PRIVATE);
        return new Snapshot(
                preferences.getString(KEY_RESPONSE, ""),
                preferences.getString(KEY_STATUS, "")
        );
    }

    static void saveData(Context context, String response, String status) {
        preferences(context).edit()
                .putString(KEY_RESPONSE, response)
                .putString(KEY_STATUS, status)
                .apply();
    }

    static void saveStatus(Context context, String status) {
        preferences(context).edit().putString(KEY_STATUS, status).apply();
    }

    static boolean hasAccounts(Snapshot snapshot) {
        try {
            JSONArray accounts = new JSONObject(snapshot.response).optJSONArray("accounts");
            return accounts != null && accounts.length() > 0;
        } catch (Exception ignored) {
            return false;
        }
    }

    private static SharedPreferences preferences(Context context) {
        return context.getSharedPreferences(FILE_NAME, Context.MODE_PRIVATE);
    }

    static final class Snapshot {
        final String response;
        final String status;

        Snapshot(String response, String status) {
            this.response = response == null ? "" : response;
            this.status = status == null ? "" : status;
        }
    }
}
