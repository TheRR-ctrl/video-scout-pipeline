package com.mesaderevision.nativa;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.system.Os;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * Deja el pipeline listo para correr: lo descomprime en una carpeta
 * escribible y pone ffmpeg a tiro.
 *
 * Los dos rodeos que hay aquí no son rodeos:
 *
 * 1. El pipeline se descomprime en vez de correr desde el APK porque sus
 *    veinte módulos cuelgan pipeline_state/, config.json y los videos de
 *    os.path.dirname(__file__). Desde el APK ese directorio sería de solo
 *    lectura; descomprimido, no.
 *
 * 2. ffmpeg llega por un enlace simbólico al directorio de librerías del
 *    APK porque desde Android 10 una app no puede ejecutar un archivo de su
 *    propia carpeta de datos (W^X). Copiarlo a filesDir y ejecutarlo da
 *    EACCES; enlazarlo funciona porque quien ejecuta es el archivo real,
 *    que está donde Android sí lo permite.
 */
final class Arranque {

    private static final String TAG = "Arranque";
    private static final String ZIP = "pipeline.zip";
    /** El pipeline descomprimido. Aquí nacen pipeline_state/, guion.txt… */
    private static final String DIR_PIPELINE = "pipeline";
    /** Los enlaces a los binarios del APK, para tenerlos con su nombre real. */
    private static final String DIR_BIN = "bin";
    /** Recuerda con qué versión de la app se descomprimió lo de dentro. */
    private static final String SELLO = ".version";

    private Arranque() { }

    static File dirPipeline(Context c) {
        return new File(c.getFilesDir(), DIR_PIPELINE);
    }

    static File dirBin(Context c) {
        return new File(c.getFilesDir(), DIR_BIN);
    }

    /**
     * Descomprime el pipeline si hace falta y prepara los binarios.
     *
     * Solo rehace el trabajo cuando cambia la versión de la app, así que
     * arrancar cuesta lo mismo que abrir cualquier otra app a partir de la
     * segunda vez.
     */
    static void preparar(Context c) throws IOException {
        File destino = dirPipeline(c);
        File sello = new File(destino, SELLO);
        String version = versionApp(c);

        if (!version.equals(leer(sello))) {
            // Ojo: se borra el código, no el estado. pipeline_state/,
            // config.json, secretos.env y los videos viven en esta misma
            // carpeta y tienen que sobrevivir a una actualización — borrar
            // el directorio entero equivaldría a reinstalar de cero y
            // perder el registro de lo ya publicado.
            borrarCodigo(destino);
            destino.mkdirs();
            descomprimir(c, destino);
            escribir(sello, version);
            Log.i(TAG, "Pipeline desplegado para la versión " + version);
        }

        prepararBinarios(c);
    }

    /**
     * Los nombres que trae el zip son código; lo demás es del teléfono y se
     * queda. Sin esta distinción, actualizar la app sería empezar de cero.
     */
    private static void borrarCodigo(File dir) {
        File[] hijos = dir.listFiles();
        if (hijos == null) return;
        for (File f : hijos) {
            String n = f.getName();
            boolean esCodigo = n.endsWith(".py")
                    || n.equals("web") || n.equals("fuentes")
                    || n.equals("__pycache__")
                    || n.equals(SELLO)
                    || n.equals("config.example.json")
                    || n.equals("config_trends.ejemplo.json");
            if (esCodigo) borrarRecursivo(f);
        }
    }

    private static void borrarRecursivo(File f) {
        if (f.isDirectory()) {
            File[] hijos = f.listFiles();
            if (hijos != null) for (File h : hijos) borrarRecursivo(h);
        }
        // Da igual si falla: lo que venga detrás lo sobrescribe.
        //noinspection ResultOfMethodCallIgnored
        f.delete();
    }

    private static void descomprimir(Context c, File destino) throws IOException {
        byte[] buf = new byte[32 * 1024];
        try (InputStream in = c.getAssets().open(ZIP);
             ZipInputStream zip = new ZipInputStream(in)) {
            ZipEntry e;
            while ((e = zip.getNextEntry()) != null) {
                File salida = new File(destino, e.getName());

                // Un zip puede traer nombres con ../ y escribir fuera del
                // destino (Zip Slip). Este zip lo hacemos nosotros, pero la
                // comprobación cuesta dos líneas y el fallo es silencioso.
                if (!salida.getCanonicalPath().startsWith(destino.getCanonicalPath() + File.separator)) {
                    throw new IOException("Entrada fuera de sitio en el zip: " + e.getName());
                }

                if (e.isDirectory()) {
                    salida.mkdirs();
                } else {
                    File padre = salida.getParentFile();
                    if (padre != null) padre.mkdirs();
                    try (OutputStream out = new FileOutputStream(salida)) {
                        int n;
                        while ((n = zip.read(buf)) > 0) out.write(buf, 0, n);
                    }
                }
                zip.closeEntry();
            }
        }
    }

    /**
     * Enlaza los binarios del APK con el nombre que el pipeline espera.
     *
     * El binario viaja como "libffmpeg.so" porque es la única forma de que
     * Android lo empaquete donde se puede ejecutar. Pero el pipeline llama
     * a "ffmpeg" y usa shutil.which, así que hace falta que exista algo con
     * ese nombre exacto en el PATH.
     */
    private static void prepararBinarios(Context c) {
        File bin = dirBin(c);
        bin.mkdirs();
        String libs = c.getApplicationInfo().nativeLibraryDir;

        // ffprobe también: el pipeline lo llama 13 veces para medir
        // duraciones y resoluciones antes de renderizar.
        enlazar(new File(libs, "libffmpeg.so"), new File(bin, "ffmpeg"));
        enlazar(new File(libs, "libffprobe.so"), new File(bin, "ffprobe"));
    }

    private static void enlazar(File real, File enlace) {
        if (!real.exists()) {
            // Sin el binario, el panel arranca igual y el pipeline falla al
            // renderizar con un mensaje claro de ffmpeg. Es mejor que no
            // abrir la app.
            Log.w(TAG, "No está " + real.getName() + " en el APK");
            return;
        }
        try {
            if (enlace.exists()) {
                //noinspection ResultOfMethodCallIgnored
                enlace.delete();
            }
            Os.symlink(real.getAbsolutePath(), enlace.getAbsolutePath());
        } catch (Exception e) {
            Log.w(TAG, "No se pudo enlazar " + enlace.getName() + ": " + e);
        }
    }

    private static String versionApp(Context c) {
        try {
            PackageInfo pi = c.getPackageManager().getPackageInfo(c.getPackageName(), 0);
            return pi.versionName + "-" + pi.getLongVersionCode();
        } catch (PackageManager.NameNotFoundException e) {
            return "desconocida";
        }
    }

    private static String leer(File f) {
        if (!f.exists()) return "";
        try (InputStream in = new java.io.FileInputStream(f)) {
            byte[] b = new byte[(int) f.length()];
            int n = in.read(b);
            return n > 0 ? new String(b, 0, n, "UTF-8") : "";
        } catch (IOException e) {
            return "";
        }
    }

    private static void escribir(File f, String texto) throws IOException {
        try (OutputStream out = new FileOutputStream(f)) {
            out.write(texto.getBytes("UTF-8"));
        }
    }
}
