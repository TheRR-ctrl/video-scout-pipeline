package com.mesaderevision.nativa;

import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;

/**
 * La misma mesa de revisión, sin Termux debajo.
 *
 * Al lado de la versión con Termux (módulo :app) esta Activity es más
 * simple, y la diferencia dice exactamente qué se gana: allí había que
 * comprobar si Termux estaba instalado, si el permiso estaba concedido y si
 * aceptaba la orden, con una pantalla de ayuda para cada uno de los tres
 * fallos. Aquí el servidor es parte de la app, así que o arranca o la app
 * está rota — no hay estados intermedios que explicarle a nadie.
 */
public class MainActivity extends android.app.Activity {

    private static final String HOST = "127.0.0.1";
    private static final String PANEL = "http://" + HOST + ":" + ServicioPanel.PUERTO + "/";
    private static final String ESPERA = "file:///android_asset/arrancando.html";

    /** Arrancar CPython y desplegar el pipeline la primera vez lleva lo suyo. */
    private static final long ESPERA_MAX_MS = 60000;
    private static final int SONDEO_MS = 300;

    private WebView web;
    private final Handler enPantalla = new Handler(Looper.getMainLooper());
    private boolean panelCargado = false;
    private boolean buscando = false;

    @Override
    protected void onCreate(Bundle guardado) {
        super.onCreate(guardado);

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccessFromFileURLs(false);
        s.setAllowUniversalAccessFromFileURLs(false);
        s.setAllowContentAccess(false);
        s.setSupportZoom(false);

        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                Uri u = r.getUrl();
                if (u == null) return false;
                // El panel, dentro; la fuente en Reddit o el video en
                // YouTube, en el navegador de verdad.
                if (HOST.equals(u.getHost())) return false;
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (ActivityNotFoundException e) {
                    return true;
                }
                return true;
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                if (r != null && r.isForMainFrame()) {
                    panelCargado = false;
                    web.loadUrl(ESPERA);
                    esperarAlPanel();
                }
            }
        });

        setContentView(web);
        web.loadUrl(ESPERA);

        startForegroundService(new Intent(this, ServicioPanel.class));
        esperarAlPanel();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (!panelCargado && !buscando) esperarAlPanel();
    }

    private void esperarAlPanel() {
        if (buscando) return;
        buscando = true;

        new Thread(() -> {
            long limite = SystemClock.elapsedRealtime() + ESPERA_MAX_MS;
            boolean vivo = puertoVivo();
            while (!vivo && SystemClock.elapsedRealtime() < limite) {
                SystemClock.sleep(SONDEO_MS);
                vivo = puertoVivo();
            }
            final boolean ok = vivo;
            enPantalla.post(() -> {
                buscando = false;
                if (ok) {
                    panelCargado = true;
                    web.loadUrl(PANEL);
                } else {
                    // 60 segundos sin que Flask escuche no es lentitud: es
                    // que algo reventó al importar el pipeline, y eso se lee
                    // en logcat con la etiqueta ServicioPanel.
                    web.evaluateJavascript("window.tardando && tardando()", null);
                }
            });
        }, "espera-panel").start();
    }

    private static boolean puertoVivo() {
        Socket s = new Socket();
        try {
            s.connect(new InetSocketAddress(HOST, ServicioPanel.PUERTO), 250);
            return true;
        } catch (IOException e) {
            return false;
        } finally {
            try { s.close(); } catch (IOException ignorado) { }
        }
    }

    @Override
    public void onBackPressed() {
        if (panelCargado && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        // Ojo: no se para el servicio. Cerrar la ventana no puede cancelar
        // un render a medias — para eso está el botón "Apagar" de la
        // notificación, que es explícito.
        if (web != null) {
            setContentView(new android.view.View(this));
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }
}
