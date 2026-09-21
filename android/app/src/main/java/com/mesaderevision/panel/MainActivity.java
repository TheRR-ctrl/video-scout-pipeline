package com.mesaderevision.panel;

import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
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
 * La app entera: un WebView apuntando al panel de Termux.
 *
 * Lo único que hace de verdad, y que una página web nunca podrá hacer, es
 * arrancar el servidor. Ningún navegador deja que una página lance Python en
 * tu teléfono — con razón. Una app instalada sí puede pedírselo a Termux con
 * el intent RUN_COMMAND, y eso convierte "abre Termux, escribe panel, cámbiate
 * a Chrome" en tocar un icono.
 *
 * El orden es siempre el mismo:
 *
 *   1. ¿Contesta algo en 127.0.0.1:8770? Entonces se entra y ya está. Da
 *      igual quién lo arrancó: el widget, el autoarranque de .bashrc o tú
 *      a mano.
 *   2. Si no, se le pide a Termux que corra `panel-servidor` y se espera.
 *   3. Si algo falla —Termux no está, falta el permiso, nadie contesta— se
 *      enseña assets/ayuda.html diciendo exactamente cuál de las tres cosas
 *      falló. Nunca una pantalla en blanco.
 *
 * Sin AndroidX ni ninguna otra dependencia, a propósito: es una Activity y
 * un WebView. Lo que no se importa no se puede romper en la siguiente
 * actualización.
 */
public class MainActivity extends android.app.Activity {

    private static final String HOST = "127.0.0.1";
    private static final int PUERTO = 8770;      // el mismo de servidor.py
    private static final String PANEL = "http://" + HOST + ":" + PUERTO + "/";
    private static final String AYUDA = "file:///android_asset/ayuda.html";

    private static final String TERMUX = "com.termux";
    private static final String SERVICIO = "com.termux.app.RunCommandService";
    private static final String ACCION = "com.termux.RUN_COMMAND";
    private static final String PERMISO = "com.termux.permission.RUN_COMMAND";

    /**
     * Lo deja instalar_panel.sh. Es `panel` sin --abrir: aquí el navegador
     * es esta misma app, así que no hay que abrir Chrome encima.
     *
     * Se llama a un ejecutable del PATH de Termux y no a "python
     * /ruta/servidor.py" a posta: así la ruta del repo la sabe el teléfono,
     * que es quien lo clonó, y no queda escrita dentro del APK.
     */
    private static final String ARRANCADOR =
            "/data/data/com.termux/files/usr/bin/panel-servidor";
    private static final String CASA = "/data/data/com.termux/files/home";

    private static final int PIDE_PERMISO = 1;
    /** Arrancar Python en un teléfono dormido no es instantáneo. */
    private static final long ESPERA_MAX_MS = 30000;
    private static final int SONDEO_MS = 400;

    private WebView web;
    private final Handler enPantalla = new Handler(Looper.getMainLooper());
    private boolean panelCargado = false;
    private boolean buscando = false;
    private String estadoAyuda = "buscando";

    @Override
    protected void onCreate(Bundle guardado) {
        super.onCreate(guardado);

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        // El panel enseña previsualizaciones de video; sin esto no arrancan
        // solas y el botón de reproducir no siempre aparece.
        s.setMediaPlaybackRequiresUserGesture(false);
        // Nada de leer el disco del teléfono desde el WebView: lo único local
        // que carga es assets/ayuda.html, que no necesita salir de ahí.
        s.setAllowFileAccessFromFileURLs(false);
        s.setAllowUniversalAccessFromFileURLs(false);
        s.setAllowContentAccess(false);
        s.setSupportZoom(false);

        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                return desviar(r.getUrl());
            }

            @Override
            public void onPageFinished(WebView v, String url) {
                // La ayuda se carga una vez y luego cambia de estado por JS.
                // Si el estado llegó mientras la página aún cargaba, se
                // habría perdido; aquí se vuelve a aplicar.
                if (url != null && url.startsWith(AYUDA)) {
                    v.evaluateJavascript("window.pintar && pintar('" + estadoAyuda + "')", null);
                }
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                // Que se caiga el servidor con el panel abierto es normal:
                // el vigilante de servidor.py se apaga solo al rato. En vez
                // del error del WebView, la pantalla de siempre.
                if (r != null && r.isForMainFrame()) {
                    panelCargado = false;
                    mostrarAyuda("apagado");
                }
            }
        });

        setContentView(web);
        mostrarAyuda("buscando");
        buscarPanel(true);
    }

    @Override
    protected void onResume() {
        super.onResume();
        // Volver de Termux tras arrancarlo a mano tiene que entrar solo.
        // Aquí no se pide arranque: solo se mira si ya hay alguien.
        if (!panelCargado && !buscando) buscarPanel(false);
    }

    /** true = lo consumimos nosotros; false = que lo cargue el WebView. */
    private boolean desviar(Uri u) {
        if (u == null) return false;
        String esquema = u.getScheme();

        if ("mesa".equals(esquema)) {
            String que = u.getHost();
            if ("reintentar".equals(que)) buscarPanel(true);
            else if ("termux".equals(que)) abrirTermux();
            return true;
        }

        // El panel, dentro. Cualquier otro enlace (la fuente en Reddit, el
        // video en YouTube) al navegador de verdad: este WebView no tiene
        // barra de direcciones ni sesiones iniciadas.
        if (HOST.equals(u.getHost())) return false;

        try {
            startActivity(new Intent(Intent.ACTION_VIEW, u));
        } catch (ActivityNotFoundException e) {
            // Sin nada que lo abra, mejor no hacer nada que reventar.
        }
        return true;
    }

    private void buscarPanel(final boolean arrancarSiHaceFalta) {
        if (buscando) return;
        buscando = true;

        new Thread(new Runnable() {
            @Override
            public void run() {
                boolean vivo = puertoVivo();

                if (!vivo && arrancarSiHaceFalta) {
                    if (!hayTermux()) { acabar("sin-termux", false); return; }

                    if (checkSelfPermission(PERMISO) != PackageManager.PERMISSION_GRANTED) {
                        enPantalla.post(new Runnable() {
                            @Override public void run() {
                                buscando = false;
                                mostrarAyuda("sin-permiso");
                                requestPermissions(new String[]{PERMISO}, PIDE_PERMISO);
                            }
                        });
                        return;
                    }

                    if (!pedirArranque()) { acabar("sin-arranque", false); return; }

                    enPantalla.post(new Runnable() {
                        @Override public void run() { mostrarAyuda("arrancando"); }
                    });

                    long limite = SystemClock.elapsedRealtime() + ESPERA_MAX_MS;
                    while (!vivo && SystemClock.elapsedRealtime() < limite) {
                        SystemClock.sleep(600);
                        vivo = puertoVivo();
                    }
                }

                if (vivo) acabar(null, true);
                else acabar(arrancarSiHaceFalta ? "sin-respuesta" : "apagado", false);
            }
        }).start();
    }

    private void acabar(final String estado, final boolean entrar) {
        enPantalla.post(new Runnable() {
            @Override
            public void run() {
                buscando = false;
                if (entrar) cargarPanel();
                else mostrarAyuda(estado);
            }
        });
    }

    private static boolean puertoVivo() {
        Socket s = new Socket();
        try {
            s.connect(new InetSocketAddress(HOST, PUERTO), SONDEO_MS);
            return true;
        } catch (IOException e) {
            return false;
        } finally {
            try { s.close(); } catch (IOException ignorado) { }
        }
    }

    private boolean hayTermux() {
        try {
            getPackageManager().getPackageInfo(TERMUX, 0);
            return true;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
    }

    /**
     * El intent que arranca el servidor. Devuelve false si Termux ni lo
     * acepta — lo normal es que falte allow-external-apps=true, que es un
     * SecurityException y no un fallo de esta app.
     */
    private boolean pedirArranque() {
        Intent i = new Intent();
        i.setClassName(TERMUX, SERVICIO);
        i.setAction(ACCION);
        i.putExtra("com.termux.RUN_COMMAND_PATH", ARRANCADOR);
        i.putExtra("com.termux.RUN_COMMAND_WORKDIR", CASA);
        // En segundo plano: no queremos que Termux salte a primer plano y
        // tape la app justo cuando está arrancando lo que vamos a mirar.
        i.putExtra("com.termux.RUN_COMMAND_BACKGROUND", true);
        try {
            startForegroundService(i);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    private void abrirTermux() {
        Intent i = getPackageManager().getLaunchIntentForPackage(TERMUX);
        if (i != null) startActivity(i);
    }

    private void cargarPanel() {
        panelCargado = true;
        web.loadUrl(PANEL);
    }

    private void mostrarAyuda(String estado) {
        panelCargado = false;
        estadoAyuda = estado;
        String actual = web.getUrl();
        if (actual != null && actual.startsWith(AYUDA)) {
            // Ya está cargada: cambiarle el estado, no recargarla. Recargar
            // con otro #hash no vuelve a ejecutar el script de la página.
            web.evaluateJavascript("window.pintar && pintar('" + estado + "')", null);
        } else {
            web.loadUrl(AYUDA + "#" + estado);
        }
    }

    @Override
    public void onRequestPermissionsResult(int codigo, String[] permisos, int[] resultados) {
        super.onRequestPermissionsResult(codigo, permisos, resultados);
        if (codigo != PIDE_PERMISO) return;
        if (resultados.length > 0 && resultados[0] == PackageManager.PERMISSION_GRANTED) {
            buscarPanel(true);
        } else {
            mostrarAyuda("sin-permiso");
        }
    }

    /**
     * Dentro del panel, "atrás" es navegar; fuera, salir.
     *
     * Sí, onBackPressed está marcado como obsoleto desde Android 13 — el
     * javac lo avisa al compilar. Sigue siendo el camino correcto aquí: el
     * sustituto (OnBackInvokedCallback) solo entra en juego si la app activa
     * android:enableOnBackInvokedCallback, y esta no lo hace. El día que
     * targetSdk pase de 35 habrá que mirarlo otra vez.
     */
    @Override
    public void onBackPressed() {
        if (panelCargado && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        // El WebView se queda con la Activity si no se suelta a mano.
        if (web != null) {
            setContentView(new android.view.View(this));
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }
}
