package br.com.monitoria.android;

import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;
import android.widget.RemoteViewsService;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.text.NumberFormat;

public final class WidgetAccountService extends RemoteViewsService {
    @Override
    public RemoteViewsFactory onGetViewFactory(Intent intent) {
        return new AccountFactory(getApplicationContext());
    }

    private static final class AccountFactory implements RemoteViewsFactory {
        private final Context context;
        private final List<JSONObject> accounts = new ArrayList<>();

        AccountFactory(Context context) {
            this.context = context;
        }

        @Override
        public void onCreate() {
        }

        @Override
        public void onDataSetChanged() {
            accounts.clear();
            try {
                String response = WidgetSnapshotStore.load(context).response;
                JSONArray values = new JSONObject(response).optJSONArray("accounts");
                if (values == null) {
                    return;
                }
                for (int index = 0; index < values.length(); index++) {
                    JSONObject account = values.optJSONObject(index);
                    if (account != null) {
                        accounts.add(account);
                    }
                }
            } catch (Exception ignored) {
                // O widget continua exibindo o estado salvo quando a resposta for invalida.
            }
        }

        @Override
        public void onDestroy() {
            accounts.clear();
        }

        @Override
        public int getCount() {
            return accounts.size();
        }

        @Override
        public RemoteViews getViewAt(int position) {
            if (position < 0 || position >= accounts.size()) {
                return null;
            }

            JSONObject account = accounts.get(position);
            RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.monitor_widget_account);
            String type = account.optString("type", "");
            String label = account.optString("label", account.optString("typeLabel", "Conta"));

            views.setImageViewResource(R.id.widget_account_logo, providerLogo(type));
            views.setTextViewText(R.id.widget_account_label, label);
            views.setTextViewText(R.id.widget_account_type, account.optString("typeLabel", type));

            String detail = account.optString("detail", "");
            views.setViewVisibility(
                    R.id.widget_account_detail,
                    detail.isEmpty() ? android.view.View.GONE : android.view.View.VISIBLE
            );
            views.setTextViewText(R.id.widget_account_detail, detail);

            String error = account.optString("error", "");
            if (!error.isEmpty()) {
                views.setViewVisibility(R.id.widget_account_error, android.view.View.VISIBLE);
                views.setTextViewText(R.id.widget_account_error, error);
                hideMetrics(views);
            } else {
                views.setViewVisibility(R.id.widget_account_error, android.view.View.GONE);
                JSONArray metrics = account.optJSONArray("metrics");
                bindMetric(views, metrics == null ? null : metrics.optJSONObject(0), 1);
                bindMetric(views, metrics == null ? null : metrics.optJSONObject(1), 2);
            }

            String stale = account.optString("stale", "");
            String footer = stale.isEmpty() ? updatedAgo(account.optLong("updatedAt", 0)) : stale;
            views.setTextViewText(R.id.widget_account_updated, footer);
            views.setOnClickFillInIntent(R.id.widget_account_card, new Intent());
            return views;
        }

        @Override
        public RemoteViews getLoadingView() {
            return null;
        }

        @Override
        public int getViewTypeCount() {
            return 1;
        }

        @Override
        public long getItemId(int position) {
            return accounts.get(position).optString("id", String.valueOf(position)).hashCode();
        }

        @Override
        public boolean hasStableIds() {
            return true;
        }

        private void hideMetrics(RemoteViews views) {
            views.setViewVisibility(R.id.widget_metric_one, android.view.View.GONE);
            views.setViewVisibility(R.id.widget_metric_two, android.view.View.GONE);
        }

        private void bindMetric(RemoteViews views, JSONObject metric, int position) {
            int containerId = position == 1 ? R.id.widget_metric_one : R.id.widget_metric_two;
            int labelId = position == 1 ? R.id.widget_metric_one_label : R.id.widget_metric_two_label;
            int valueId = position == 1 ? R.id.widget_metric_one_value : R.id.widget_metric_two_value;
            int progressId = position == 1 ? R.id.widget_metric_one_progress : R.id.widget_metric_two_progress;

            if (metric == null) {
                views.setViewVisibility(containerId, android.view.View.GONE);
                return;
            }
            views.setViewVisibility(containerId, android.view.View.VISIBLE);
            views.setTextViewText(labelId, metricLabel(metric.optString("label", "")));
            if (metric.has("percent")) {
                double percent = Math.max(0, Math.min(100, metric.optDouble("percent", 0)));
                String reset = metric.optString("reset", "");
                views.setTextViewText(valueId, formatPercent(percent) + (reset.isEmpty() ? "" : "  " + reset));
                views.setViewVisibility(progressId, android.view.View.VISIBLE);
                views.setProgressBar(progressId, 100, (int) Math.round(percent), false);
            } else {
                Object value = metric.opt("value");
                String unit = metric.optString("unit", "");
                views.setTextViewText(valueId, formatValue(value) + (unit.isEmpty() ? "" : " " + unit));
                views.setViewVisibility(progressId, android.view.View.GONE);
            }
        }

        private String metricLabel(String label) {
            return "Limite semanal".equals(label) ? "Semanal" : label;
        }

        private String formatPercent(double value) {
            if (value == Math.rint(value)) {
                return Math.round(value) + "%";
            }
            return String.format(Locale.getDefault(), "%.1f%%", value);
        }

        private String formatValue(Object value) {
            if (value instanceof Number) {
                NumberFormat format = NumberFormat.getNumberInstance(Locale.getDefault());
                format.setMaximumFractionDigits(4);
                return format.format(((Number) value).doubleValue());
            }
            return value == null || JSONObject.NULL.equals(value) ? "-" : String.valueOf(value);
        }

        private int providerLogo(String type) {
            if ("claude".equals(type)) {
                return R.drawable.widget_claude;
            }
            if ("codex".equals(type)) {
                return R.drawable.widget_codex;
            }
            if ("deepseek".equals(type)) {
                return R.drawable.widget_deepseek;
            }
            if ("openrouter".equals(type)) {
                return R.drawable.widget_openrouter;
            }
            return R.drawable.widget_provider_mark;
        }

        private String updatedAgo(long timestamp) {
            if (timestamp <= 0) {
                return "ainda nao atualizado";
            }
            long minutes = Math.max(0, (System.currentTimeMillis() - timestamp) / 60_000);
            if (minutes < 1) {
                return "atualizado agora";
            }
            if (minutes == 1) {
                return "atualizado ha 1 min";
            }
            return "atualizado ha " + minutes + " min";
        }
    }
}
