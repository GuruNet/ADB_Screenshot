package io.github.adbscreenshot.gpstracker;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.location.LocationManager;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.io.IOException;

public final class TrackerActivity extends Activity {
    private String session;
    private TextView message;
    private Button start;
    private Button stop;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(36, 48, 36, 36);
        layout.setGravity(Gravity.CENTER_HORIZONTAL);
        message = new TextView(this);
        message.setTextSize(19);
        layout.addView(message);
        start = new Button(this);
        start.setText("GPS 기록 시작");
        start.setVisibility(android.view.View.GONE);
        start.setOnClickListener(new android.view.View.OnClickListener() {
            @Override public void onClick(android.view.View view) { requestStart(); }
        });
        layout.addView(start);
        stop = new Button(this);
        stop.setText("GPS 기록 중지");
        stop.setVisibility(android.view.View.GONE);
        stop.setOnClickListener(new android.view.View.OnClickListener() {
            @Override public void onClick(android.view.View view) { stopSession(session); }
        });
        layout.addView(stop);
        Button back = new Button(this);
        back.setText("지도 앱으로 돌아가기");
        back.setOnClickListener(new android.view.View.OnClickListener() {
            @Override public void onClick(android.view.View view) { finish(); }
        });
        layout.addView(back);
        setContentView(layout);
        handle(getIntent());
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handle(intent);
    }

    private void handle(Intent intent) {
        String action = intent.getStringExtra("action");
        String requested = intent.getStringExtra("session");
        if ("start".equals(action)) {
            if (!Journal.validSession(requested)) { message.setText("잘못된 기록 요청입니다."); return; }
            session = requested;
            try { Journal.append(this, session, Journal.event("awaiting_confirmation")); } catch (IOException ignored) { }
            start.setVisibility(android.view.View.VISIBLE);
            stop.setVisibility(android.view.View.VISIBLE);
            message.setText("PC에서 실제 이동 경로 기록을 요청했습니다. 시작하려면 아래 버튼을 누르고 정확한 위치 및 알림을 허용해 주세요.");
        } else if ("stop".equals(action)) {
            if (!Journal.validSession(requested)) { message.setText("잘못된 기록 요청입니다."); return; }
            stopSession(requested);
        } else {
            session = getSharedPreferences("tracking", MODE_PRIVATE).getString("active_session", null);
            if (Journal.validSession(session) && TrackerService.isRunning(session)) {
                start.setVisibility(android.view.View.GONE);
                stop.setVisibility(android.view.View.VISIBLE);
                message.setText("GPS 이동 경로를 기록하고 있습니다. 중지하려면 아래 버튼을 누르세요.");
            } else {
                if (Journal.validSession(session)) {
                    try { Journal.append(this, session, Journal.event("interrupted")); } catch (IOException ignored) { }
                    getSharedPreferences("tracking", MODE_PRIVATE).edit().remove("active_session").apply();
                }
                start.setVisibility(android.view.View.GONE);
                stop.setVisibility(android.view.View.GONE);
                message.setText("이 앱은 사용자가 PC에서 시작한 GPS 이동 경로를 기록합니다.\n기록 중에는 알림이 표시됩니다.");
            }
        }
    }

    private void stopSession(String requested) {
        start.setVisibility(android.view.View.GONE);
        stop.setVisibility(android.view.View.GONE);
        if (TrackerService.isRunning(requested)) {
            Intent service = new Intent(this, TrackerService.class);
            service.setAction("stop");
            service.putExtra("session", requested);
            startService(service);
        } else {
            try { Journal.append(this, requested, Journal.event("stopped")); } catch (IOException ignored) { }
        }
        message.setText("GPS 기록을 종료했습니다. PC에서 결과를 동기화하세요.");
        finish();
    }

    private void requestStart() {
        boolean location = checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED;
        boolean notifications = Build.VERSION.SDK_INT < 33 || checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED;
        if (!location || !notifications) {
            try { Journal.append(this, session, Journal.event("permission_required")); } catch (IOException ignored) { }
            if (Build.VERSION.SDK_INT >= 33) {
                requestPermissions(new String[] {Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION, Manifest.permission.POST_NOTIFICATIONS}, 1);
            } else {
                requestPermissions(new String[] {Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, 1);
            }
        } else begin();
    }

    private void begin() {
        LocationManager manager = (LocationManager) getSystemService(LOCATION_SERVICE);
        if (!manager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
            try { Journal.append(this, session, Journal.event("location_disabled")); } catch (IOException ignored) { }
            message.setText("GPS가 꺼져 있습니다. 위치 서비스를 켠 다음 PC에서 다시 시작해 주세요.");
            return;
        }
        start.setVisibility(android.view.View.GONE);
        stop.setVisibility(android.view.View.VISIBLE);
        Intent service = new Intent(this, TrackerService.class);
        service.setAction("start");
        service.putExtra("session", session);
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(service); else startService(service);
        message.setText("GPS 기록을 시작합니다. 알림이 표시되는 동안 지도 앱으로 돌아가세요.\n실제 GPS 수신까지 시간이 걸릴 수 있습니다.");
    }

    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request, permissions, results);
        if (request != 1) return;
        boolean location = checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED;
        boolean notifications = Build.VERSION.SDK_INT < 33 || checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED;
        if (location && notifications) begin();
        else {
            try { Journal.append(this, session, Journal.event("permission_denied")); } catch (IOException ignored) { }
            message.setText("정확한 위치 또는 알림이 허용되지 않아 GPS 기록을 시작하지 못했습니다. 설정에서 권한을 확인해 주세요.");
        }
    }
}
