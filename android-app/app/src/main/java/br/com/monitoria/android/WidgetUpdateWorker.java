package br.com.monitoria.android;

import android.content.Context;
import android.util.Base64;

import androidx.annotation.NonNull;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

public final class WidgetUpdateWorker extends Worker {
    private static final int CONNECT_TIMEOUT_MS = 10_000;
    private static final int READ_TIMEOUT_MS = 45_000;

    public WidgetUpdateWorker(@NonNull Context context, @NonNull WorkerParameters parameters) {
        super(context, parameters);
    }

    @NonNull
    @Override
    public Result doWork() {
        Context context = getApplicationContext();
        MonitorConfig.Config config = MonitorConfig.load(context);
        if (!config.hasServer()) {
            MonitorWidgetProvider.showStatus(context, context.getString(R.string.widget_not_configured));
            return Result.success();
        }

        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(MonitorConfig.statusUrl(context, true)).openConnection();
            connection.setRequestMethod("GET");
            connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
            connection.setReadTimeout(READ_TIMEOUT_MS);
            connection.setRequestProperty("Accept", "application/json");
            if (!config.username.isEmpty() || !config.password.isEmpty()) {
                String credentials = config.username + ":" + config.password;
                String encoded = Base64.encodeToString(
                        credentials.getBytes(StandardCharsets.UTF_8),
                        Base64.NO_WRAP
                );
                connection.setRequestProperty("Authorization", "Basic " + encoded);
            }

            int responseCode = connection.getResponseCode();
            if (responseCode < 200 || responseCode >= 300) {
                MonitorWidgetProvider.showStatus(
                        context,
                        context.getString(R.string.widget_request_failed, responseCode)
                );
                return responseCode >= 500 ? Result.retry() : Result.success();
            }

            String response = readBody(connection.getInputStream());
            MonitorWidgetProvider.renderData(
                    context,
                    response,
                    context.getString(
                            R.string.widget_updated_at,
                            new SimpleDateFormat("HH:mm", Locale.getDefault()).format(new Date())
                    )
            );
            return Result.success();
        } catch (Exception exception) {
            MonitorWidgetProvider.showStatus(
                    context,
                    context.getString(R.string.widget_connection_failed)
            );
            return Result.retry();
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private static String readBody(InputStream input) throws IOException {
        try (InputStream stream = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int count;
            while ((count = stream.read(buffer)) != -1) {
                output.write(buffer, 0, count);
            }
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }

}
