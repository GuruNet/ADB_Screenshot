package io.github.adbscreenshot.gpstracker;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Build;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import org.json.JSONObject;
import java.io.IOException;
import java.time.Instant;

public final class TrackerService extends Service implements LocationListener {
    private static final String CHANNEL = "gps_tracking";
    private static TrackerService instance;
    private LocationManager manager;
    private String session;
    private boolean ended;
    private long lastElapsedNanos = -1;

    static boolean isRunning(String id) {
        return instance != null && !instance.ended && id != null && id.equals(instance.session);
    }

    private boolean event(String id, String name) {
        try { Journal.append(this, id, Journal.event(name)); return true; }
        catch (IOException error) { Log.e("AdbGpsTracker", "Failed to write GPS event", error); return false; }
    }

    @Override public void onCreate() {
        super.onCreate();
        instance = this;
        manager = (LocationManager) getSystemService(LOCATION_SERVICE);
        NotificationManager notifications = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        notifications.createNotificationChannel(new NotificationChannel(CHANNEL, "GPS 이동 기록", NotificationManager.IMPORTANCE_LOW));
        Notification notification = buildNotification(null);
        if (Build.VERSION.SDK_INT >= 29) startForeground(1042, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);
        else startForeground(1042, notification);
    }

    private Notification buildNotification(String id) {
        Intent open = new Intent(this, TrackerActivity.class);
        PendingIntent openApp = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification.Builder builder = new Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle("GPS 이동 경로 기록 중")
            .setContentText("알림 또는 보조 앱에서 기록을 중지할 수 있습니다.")
            .setContentIntent(openApp).setOngoing(true);
        if (id != null) {
            Intent stop = new Intent(this, TrackerService.class).setAction("stop").putExtra("session", id);
            PendingIntent stopAction = PendingIntent.getService(this, 1, stop, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
            builder.addAction(new Notification.Action.Builder(null, "기록 중지", stopAction).build());
        }
        return builder.build();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) { stopSelf(); return START_NOT_STICKY; }
        String requested = intent.getStringExtra("session");
        if (!Journal.validSession(requested)) { if (session == null) stopSelf(); return START_NOT_STICKY; }
        if ("stop".equals(intent.getAction())) {
            if (session != null && !session.equals(requested)) { event(requested, "session_mismatch"); return START_NOT_STICKY; }
            event(requested, "stopped");
            ended = true;
            if (session != null) manager.removeUpdates(this);
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!"start".equals(intent.getAction())) { if (session == null) stopSelf(); return START_NOT_STICKY; }
        if (session != null) {
            if (!session.equals(requested)) event(requested, "already_recording");
            return START_NOT_STICKY;
        }
        session = requested;
        try {
            manager.requestLocationUpdates(LocationManager.GPS_PROVIDER, 3000L, 0f, this, Looper.getMainLooper());
            if (!event(session, "started")) {
                ended = true;
                manager.removeUpdates(this);
                stopSelf();
                return START_NOT_STICKY;
            }
            getSharedPreferences("tracking", MODE_PRIVATE).edit().putString("active_session", session).apply();
            ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).notify(1042, buildNotification(session));
        } catch (SecurityException | IllegalArgumentException error) {
            event(session, "location_error");
            ended = true;
            stopSelf();
        }
        return START_NOT_STICKY;
    }

    @Override public void onLocationChanged(Location location) {
        if (session == null || ended) return;
        long elapsed = location.getElapsedRealtimeNanos();
        long age = SystemClock.elapsedRealtimeNanos() - elapsed;
        if (location.isFromMockProvider() || elapsed <= lastElapsedNanos || age < 0 || age > 30_000_000_000L) return;
        try {
            JSONObject point = new JSONObject();
            point.put("event", "point");
            point.put("timestamp", Instant.ofEpochMilli(location.getTime()).toString());
            point.put("latitude", location.getLatitude());
            point.put("longitude", location.getLongitude());
            point.put("accuracy_m", location.hasAccuracy() ? location.getAccuracy() : JSONObject.NULL);
            point.put("altitude_m", location.hasAltitude() ? location.getAltitude() : JSONObject.NULL);
            point.put("provider", location.getProvider());
            Journal.append(this, session, point);
            lastElapsedNanos = elapsed;
        } catch (Exception error) {
            Log.e("AdbGpsTracker", "Failed to persist GPS fix", error);
            event(session, "storage_error");
            ended = true;
            manager.removeUpdates(this);
            stopSelf();
        }
    }

    @Override public void onProviderDisabled(String provider) { if (session != null) event(session, "provider_disabled"); }
    @Override public void onProviderEnabled(String provider) { if (session != null) event(session, "provider_enabled"); }
    @Override public void onDestroy() {
        if (session != null) {
            manager.removeUpdates(this);
            if (!ended) event(session, "interrupted");
            if (session.equals(getSharedPreferences("tracking", MODE_PRIVATE).getString("active_session", null))) {
                getSharedPreferences("tracking", MODE_PRIVATE).edit().remove("active_session").apply();
            }
        }
        instance = null;
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) { return null; }
}
