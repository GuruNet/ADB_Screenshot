package io.github.adbscreenshot.gpstracker;

import android.content.Context;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.Instant;

final class Journal {
    static boolean validSession(String id) { return id != null && id.matches("[0-9a-f]{32}"); }
    static String now() { return Instant.now().toString(); }

    static synchronized void append(Context context, String id, JSONObject event) throws IOException {
        if (!validSession(id)) throw new IOException("잘못된 기록 번호입니다.");
        File directory = new File(context.getFilesDir(), "tracks");
        if (!directory.isDirectory() && !directory.mkdirs()) throw new IOException("기록 폴더를 만들지 못했습니다.");
        File file = new File(directory, id + ".jsonl");
        try (FileOutputStream stream = new FileOutputStream(file, true)) {
            stream.write((event.toString() + "\n").getBytes(StandardCharsets.UTF_8));
            stream.flush();
        }
    }

    static JSONObject event(String event) {
        JSONObject result = new JSONObject();
        try { result.put("event", event); result.put("timestamp", now()); }
        catch (Exception ignored) { }
        return result;
    }
}
