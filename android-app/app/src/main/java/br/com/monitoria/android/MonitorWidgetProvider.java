package br.com.monitoria.android;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;

import androidx.work.Constraints;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.ExistingWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.OneTimeWorkRequest;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;

import java.util.concurrent.TimeUnit;

public final class MonitorWidgetProvider extends AppWidgetProvider {
    static final String ACTION_REFRESH = "br.com.monitoria.android.REFRESH_WIDGET";
    private static final String UPDATE_WORK_NAME = "monitor-widget-update";
    private static final String PERIODIC_WORK_NAME = "monitor-widget-periodic";

    @Override
    public void onUpdate(Context context, AppWidgetManager appWidgetManager, int[] appWidgetIds) {
        WidgetSnapshotStore.saveStatus(context, context.getString(R.string.widget_loading));
        render(context, appWidgetIds);
        requestUpdate(context);
        schedulePeriodicUpdates(context);
    }

    @Override
    public void onEnabled(Context context) {
        super.onEnabled(context);
        schedulePeriodicUpdates(context);
    }

    @Override
    public void onDisabled(Context context) {
        super.onDisabled(context);
        WorkManager.getInstance(context.getApplicationContext()).cancelUniqueWork(PERIODIC_WORK_NAME);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (ACTION_REFRESH.equals(intent.getAction()) || Intent.ACTION_MY_PACKAGE_REPLACED.equals(intent.getAction())) {
            if (ACTION_REFRESH.equals(intent.getAction())) {
                WidgetSnapshotStore.saveStatus(context, context.getString(R.string.widget_loading));
            }
            renderAll(context);
            requestUpdate(context);
            schedulePeriodicUpdates(context);
        }
    }

    static void schedulePeriodicUpdates(Context context) {
        Constraints constraints = new Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build();
        PeriodicWorkRequest request = new PeriodicWorkRequest.Builder(
                WidgetUpdateWorker.class,
                15,
                TimeUnit.MINUTES
        ).setConstraints(constraints).build();
        WorkManager.getInstance(context.getApplicationContext()).enqueueUniquePeriodicWork(
                PERIODIC_WORK_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                request
        );
    }

    static void requestUpdate(Context context) {
        OneTimeWorkRequest request = new OneTimeWorkRequest.Builder(WidgetUpdateWorker.class).build();
        WorkManager.getInstance(context.getApplicationContext()).enqueueUniqueWork(
                UPDATE_WORK_NAME,
                ExistingWorkPolicy.REPLACE,
                request
        );
    }

    static void renderData(Context context, String response, String status) {
        WidgetSnapshotStore.saveData(context, response, status);
        renderAll(context);
    }

    static void showStatus(Context context, String status) {
        WidgetSnapshotStore.saveStatus(context, status);
        renderAll(context);
    }

    private static void renderAll(Context context) {
        AppWidgetManager manager = AppWidgetManager.getInstance(context);
        int[] ids = manager.getAppWidgetIds(new ComponentName(context, MonitorWidgetProvider.class));
        render(context, ids);
    }

    @Override
    public void onAppWidgetOptionsChanged(
            Context context,
            AppWidgetManager appWidgetManager,
            int appWidgetId,
            android.os.Bundle newOptions
    ) {
        super.onAppWidgetOptionsChanged(context, appWidgetManager, appWidgetId, newOptions);
        render(context, new int[] {appWidgetId});
    }

    private static void render(Context context, int[] appWidgetIds) {
        AppWidgetManager manager = AppWidgetManager.getInstance(context);
        WidgetSnapshotStore.Snapshot snapshot = WidgetSnapshotStore.load(context);
        boolean hasAccounts = WidgetSnapshotStore.hasAccounts(snapshot);
        for (int appWidgetId : appWidgetIds) {
            android.os.Bundle options = manager.getAppWidgetOptions(appWidgetId);
            int widthDp = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 0);
            int heightDp = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, 0);
            boolean narrow = widthDp > 0 && widthDp < 200;
            boolean veryNarrow = widthDp > 0 && widthDp < 150;
            boolean shortHeight = heightDp > 0 && heightDp < 170;

            RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.monitor_widget);
            views.setTextViewText(R.id.widget_status, snapshot.status);
            views.setViewVisibility(
                    R.id.widget_subtitle,
                    (narrow || shortHeight) ? android.view.View.GONE : android.view.View.VISIBLE
            );
            views.setViewVisibility(
                    R.id.widget_status,
                    shortHeight ? android.view.View.GONE : android.view.View.VISIBLE
            );
            views.setViewVisibility(
                    R.id.widget_brand_mark,
                    veryNarrow ? android.view.View.GONE : android.view.View.VISIBLE
            );
            views.setTextViewText(
                    R.id.widget_refresh,
                    narrow ? "↻" : context.getString(R.string.action_refresh)
            );
            views.setTextViewText(
                    R.id.widget_empty,
                    snapshot.status.isEmpty() ? context.getString(R.string.widget_no_accounts) : snapshot.status
            );
            views.setViewVisibility(
                    R.id.widget_account_list,
                    hasAccounts ? android.view.View.VISIBLE : android.view.View.GONE
            );
            views.setViewVisibility(
                    R.id.widget_empty,
                    hasAccounts ? android.view.View.GONE : android.view.View.VISIBLE
            );
            views.setOnClickPendingIntent(R.id.widget_refresh, refreshIntent(context));
            views.setOnClickPendingIntent(R.id.widget_header, openAppIntent(context));
            views.setPendingIntentTemplate(R.id.widget_account_list, openAppIntent(context));
            Intent serviceIntent = new Intent(context, WidgetAccountService.class)
                    .putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId);
            views.setRemoteAdapter(R.id.widget_account_list, serviceIntent);
            manager.updateAppWidget(appWidgetId, views);
        }
        manager.notifyAppWidgetViewDataChanged(appWidgetIds, R.id.widget_account_list);
    }

    private static PendingIntent refreshIntent(Context context) {
        Intent refresh = new Intent(context, MonitorWidgetProvider.class).setAction(ACTION_REFRESH);
        return PendingIntent.getBroadcast(
                context,
                1,
                refresh,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
    }

    private static PendingIntent openAppIntent(Context context) {
        Intent openApp = new Intent(context, MainActivity.class);
        return PendingIntent.getActivity(
                context,
                2,
                openApp,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
    }
}
