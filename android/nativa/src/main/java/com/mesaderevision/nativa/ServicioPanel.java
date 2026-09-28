package com.mesaderevision.nativa;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

/**
 * El panel, corriendo dentro de la app.
 *
 * Es un servicio en primer plano y no un hilo de la Activity por un motivo
 * que este proyecto ya conoce de Termux: Android congela los procesos en
 * segundo plano, y un render de video dura minutos. En Termux eso se
 * resolvía con termux-wake-lock; aquí lo resuelve la notificación
 * persistente, que es el mecanismo que Android reconoce — con la ventaja de
 * que el usuario ve que hay algo corriendo y puede pararlo.
 */
public class ServicioPanel extends Service {

    private static final String TAG = "ServicioPanel";
    private static final String CANAL = "panel";
    private static final int ID_NOTIF = 1;
    static final int PUERTO = 8770;

    public static final String ACCION_PARAR = "com.mesaderevision.nativa.PARAR";

    private static volatile boolean corriendo = false;
    private PowerManager.WakeLock wakeLock;

    static boolean estaCorriendo() {
        return corriendo;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACCION_PARAR.equals(intent.getAction())) {
            // Python no se puede descargar de un proceso, así que "parar"
            // es terminar el proceso entero. Es brusco y es lo honesto: el
            // servidor deja de escuchar de verdad, no a medias.
            stopForeground(true);
            stopSelf();
            System.exit(0);
            return START_NOT_STICKY;
        }

        if (corriendo) return START_STICKY;
        corriendo = true;

        startForeground(ID_NOTIF, notificacion());
        mantenerDespierto();

        new Thread(this::arrancarPython, "panel-python").start();
        return START_STICKY;
    }

    private void arrancarPython() {
        try {
            Arranque.preparar(this);

            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(this));
            }

            Python.getInstance()
                    .getModule("arranque_panel")
                    .callAttr("iniciar",
                            Arranque.dirPipeline(this).getAbsolutePath(),
                            Arranque.dirBin(this).getAbsolutePath(),
                            PUERTO);

        } catch (Throwable t) {
            // Flask no devuelve el control salvo que algo se rompa, así que
            // llegar aquí siempre es un fallo. Sin este log, el síntoma
            // sería una app que abre y no carga nada.
            Log.e(TAG, "El panel se cayó", t);
            corriendo = false;
            stopForeground(true);
            stopSelf();
        }
    }

    private void mantenerDespierto() {
        try {
            PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
            if (pm == null) return;
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "mesa:panel");
            // Sin timeout: lo suelta onDestroy. Un render largo no puede
            // quedarse a medias porque venció un plazo arbitrario.
            wakeLock.acquire();
        } catch (Exception e) {
            Log.w(TAG, "Sin wake lock: " + e);
        }
    }

    private Notification notificacion() {
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm != null && nm.getNotificationChannel(CANAL) == null) {
            NotificationChannel canal = new NotificationChannel(
                    CANAL, "Panel", NotificationManager.IMPORTANCE_LOW);
            canal.setDescription("Mientras el panel está encendido");
            nm.createNotificationChannel(canal);
        }

        PendingIntent abrir = PendingIntent.getActivity(
                this, 0, new Intent(this, MainActivity.class),
                PendingIntent.FLAG_IMMUTABLE);

        Intent parar = new Intent(this, ServicioPanel.class).setAction(ACCION_PARAR);
        PendingIntent pararPI = PendingIntent.getService(
                this, 1, parar, PendingIntent.FLAG_IMMUTABLE);

        Notification.Builder b = new Notification.Builder(this, CANAL)
                .setContentTitle("Mesa de Revisión")
                .setContentText("El panel está corriendo")
                .setSmallIcon(android.R.drawable.ic_media_play)
                .setContentIntent(abrir)
                .setOngoing(true)
                .addAction(new Notification.Action.Builder(null, "Apagar", pararPI).build());

        return b.build();
    }

    @Override
    public void onDestroy() {
        corriendo = false;
        if (wakeLock != null && wakeLock.isHeld()) {
            try { wakeLock.release(); } catch (Exception ignorado) { }
        }
        super.onDestroy();
    }
}
