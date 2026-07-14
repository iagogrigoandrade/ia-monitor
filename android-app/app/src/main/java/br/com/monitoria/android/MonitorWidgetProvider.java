package br.com.monitoria.android;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;

import androidx.work.ExistingWorkPolicy;
import androidx.work.OneTimeWorkRequest;
import androidx.work.WorkManager;

public final class MonitorWidgetProvider extends AppWidgetProvider {
    static final String ACTION_REFRESH = "br.com.monitoria.android.REFRESH_WIDGET";
    private static final String UPDATE_WORK_NAME = "monitor-widget-update";

    @Override
    public void onUpdate(Context context, AppWidgetManager appWidgetManager, int[] appWidgetIds) {
        WidgetSnapshotStore.saveStatus(context, context.getString(R.string.widget_loading));
        render(context, appWidgetIds);
        requestUpdate(context);
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
        }
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

    private static void render(Context context, int[] appWidgetIds) {
        AppWidgetManager manager = AppWidgetManager.getInstance(context);
        WidgetSnapshotStore.Snapshot snapshot = WidgetSnapshotStore.load(context);
        boolean hasAccounts = WidgetSnapshotStore.hasAccounts(snapshot);
        for (int appWidgetId : appWidgetIds) {
            RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.monitor_widget);
            views.setTextViewText(R.id.widget_status, snapshot.status);
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
