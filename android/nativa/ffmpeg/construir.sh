#!/usr/bin/env bash
#
# Compila ffmpeg y ffprobe para Android arm64, como ejecutables.
#
# Por qué hay que compilarlo y no bajarlo hecho: no existe un ffmpeg
# ejecutable para Android, mantenido, que traiga libass. Los que traen
# libass (cropsly/ffmpeg-android) están congelados en Android 4.1; los que
# siguen vivos (Khang-NT/ffmpeg-binary-android) no traen libass ni
# fontconfig. Y libass no es opcional aquí: es quien dibuja los subtítulos
# karaoke que arma convertir_timing_a_karaoke_ass.
#
# Por qué ejecutables y no una librería tipo ffmpeg-kit: los mismos .py
# corren en tres sitios —Termux, el runner de GitHub y ahora la app— y las
# 31 llamadas del pipeline son subprocess.run(["ffmpeg", ...]). Un binario
# los deja iguales en los tres. Una librería obligaría a un camino distinto
# solo para Android, que es justo lo que hace que el código se pudra.
#
# LO QUE ESTE BINARIO NO PUEDE HACER: h264_mediacodec, el chip de video del
# teléfono. El soporte de mediacodec en ffmpeg pasa por JNI y necesita una
# JVM viva (av_jni_set_java_vm); un ejecutable suelto no tiene ninguna. Así
# que la app tiene que dejar usar_chip_android en false. El pipeline ya cae
# solo a libx264, así que no se rompe nada: se renderiza por CPU, más lento
# y con más batería. Está anotado en android/nativa/README.md.
#
# Uso:
#   ANDROID_NDK_HOME=/ruta/al/ndk bash construir.sh
#
# Deja en salida/: ffmpeg y ffprobe (arm64).

set -euo pipefail

NDK="${ANDROID_NDK_HOME:?Falta ANDROID_NDK_HOME}"
API="${API:-26}"                 # el mismo minSdk del módulo
TRABAJO="${TRABAJO:-$PWD/trabajo}"
SALIDA="${SALIDA:-$PWD/salida}"
NUCLEOS="$(nproc 2>/dev/null || echo 4)"

# Versiones fijas y no "la última": una compilación que funcionó tiene que
# seguir funcionando dentro de un año sin que nadie toque nada.
VER_X264="${VER_X264:-stable}"
VER_FFMPEG="${VER_FFMPEG:-n7.1}"
VER_ASS="${VER_ASS:-0.17.3}"
VER_FREETYPE="${VER_FREETYPE:-VER-2-13-3}"
VER_FRIBIDI="${VER_FRIBIDI:-v1.0.16}"
VER_HARFBUZZ="${VER_HARFBUZZ:-10.1.0}"

TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64"
export PATH="$TOOLCHAIN/bin:$PATH"
export CC="aarch64-linux-android$API-clang"
export CXX="aarch64-linux-android$API-clang++"
export AR="llvm-ar"
export RANLIB="llvm-ranlib"
export STRIP="llvm-strip"
export NM="llvm-nm"

PREFIJO="$TRABAJO/prefijo"
mkdir -p "$TRABAJO" "$SALIDA" "$PREFIJO"
export PKG_CONFIG_PATH="$PREFIJO/lib/pkgconfig"
export PKG_CONFIG_LIBDIR="$PREFIJO/lib/pkgconfig"

bajar() {  # bajar <url> <carpeta> [rama]
  local url="$1" dir="$2" rama="${3:-}"
  if [ -d "$TRABAJO/$dir" ]; then
    echo "  · $dir ya está"
    return
  fi
  echo "  ↓ $dir"
  if [ -n "$rama" ]; then
    git clone --depth 1 --branch "$rama" "$url" "$TRABAJO/$dir"
  else
    git clone --depth 1 "$url" "$TRABAJO/$dir"
  fi
}

echo
echo "  NDK:    $NDK"
echo "  API:    $API"
echo "  Salida: $SALIDA"
echo

# ---- 1. x264 ---------------------------------------------------------------
# El codificador por software. Es GPL, y por eso ffmpeg sale --enable-gpl.
bajar https://code.videolan.org/videolan/x264.git x264 "$VER_X264"
if [ ! -f "$PREFIJO/lib/libx264.a" ]; then
  echo "  → x264"
  cd "$TRABAJO/x264"
  ./configure \
    --prefix="$PREFIJO" \
    --host=aarch64-linux-android \
    --enable-static --disable-cli --disable-opencl \
    --enable-pic \
    --sysroot="$TOOLCHAIN/sysroot" >/dev/null
  make -j"$NUCLEOS" >/dev/null
  make install >/dev/null
fi

# ---- 2. fribidi ------------------------------------------------------------
# Texto bidireccional. libass no compila sin él.
bajar https://github.com/fribidi/fribidi.git fribidi "$VER_FRIBIDI"
if [ ! -f "$PREFIJO/lib/libfribidi.a" ]; then
  echo "  → fribidi"
  cd "$TRABAJO/fribidi"
  ./autogen.sh --prefix="$PREFIJO" --host=aarch64-linux-android \
    --enable-static --disable-shared --disable-docs >/dev/null
  # -j1 y no -j$NUCLEOS: fribidi genera tablas Unicode durante la
  # compilación y en paralelo se pisa a sí mismo. Falla una de cada dos
  # veces, que es la peor frecuencia posible para un fallo.
  make -j1 >/dev/null
  make install >/dev/null
fi

# ---- 3. freetype -----------------------------------------------------------
# Quien rasteriza las fuentes del proyecto (fuentes/).
bajar https://gitlab.freedesktop.org/freetype/freetype.git freetype "$VER_FREETYPE"
if [ ! -f "$PREFIJO/lib/libfreetype.a" ]; then
  echo "  → freetype"
  cd "$TRABAJO/freetype"
  ./autogen.sh >/dev/null 2>&1
  ./configure --prefix="$PREFIJO" --host=aarch64-linux-android \
    --enable-static --disable-shared \
    --with-zlib=no --with-bzip2=no --with-png=no --with-harfbuzz=no \
    --with-brotli=no >/dev/null
  make -j"$NUCLEOS" >/dev/null
  make install >/dev/null
fi

# ---- 3b. harfbuzz ----------------------------------------------------------
# No estaba previsto y no es opcional: libass >= 0.15 lo exige
# (PKG_CHECK_MODULES harfbuzz >= 1.2.3) y no tiene --disable-harfbuzz. Es
# quien coloca ligaduras y kerning, así que tampoco sería gratis quitarlo.
#
# Con meson y no con autotools, aunque el resto del script sea autotools:
# harfbuzz dejó de traer autotools en la 3.0, y la última que lo traía
# (2.9.1, de 2021) ya no compila con el clang del NDK 27 — su hb.hh tiene
# un "#pragma GCC diagnostic error -Wcast-function-type" que convierte en
# error un aviso que clang endureció después. No hay flag que lo calle:
# el pragma gana. Quedarse en 2.9.1 obligaría a parchear el código de
# terceros en cada NDK nuevo.
bajar https://github.com/harfbuzz/harfbuzz.git harfbuzz "$VER_HARFBUZZ"
if [ ! -f "$PREFIJO/lib/libharfbuzz.a" ]; then
  echo "  → harfbuzz"
  cd "$TRABAJO/harfbuzz"

  # meson necesita que le digan a mano para qué máquina compila.
  cat > cruzado.ini <<CROSS
[binaries]
c = '$TOOLCHAIN/bin/$CC'
cpp = '$TOOLCHAIN/bin/$CXX'
ar = '$TOOLCHAIN/bin/llvm-ar'
strip = '$TOOLCHAIN/bin/llvm-strip'
pkg-config = 'pkg-config'

[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'aarch64'
endian = 'little'
CROSS

  rm -rf construccion
  meson setup construccion \
    --cross-file cruzado.ini \
    --prefix="$PREFIJO" \
    --default-library=static \
    --buildtype=release \
    -Dfreetype=enabled \
    -Dglib=disabled -Dgobject=disabled -Dcairo=disabled -Dicu=disabled \
    -Dtests=disabled -Ddocs=disabled -Dutilities=disabled >/dev/null
  meson compile -C construccion >/dev/null
  meson install -C construccion >/dev/null
fi

# ---- 4. libass -------------------------------------------------------------
# El motor de subtítulos .ass. Sin esto, el karaoke no se dibuja.
bajar https://github.com/libass/libass.git libass "$VER_ASS"
if [ ! -f "$PREFIJO/lib/libass.a" ]; then
  echo "  → libass"
  cd "$TRABAJO/libass"
  ./autogen.sh >/dev/null 2>&1
  # Sin fontconfig a propósito: en Android no hay un sistema de fuentes que
  # consultar, y el pipeline pasa las suyas por ruta. fontconfig solo
  # añadiría una dependencia más que compilar.
  ./configure --prefix="$PREFIJO" --host=aarch64-linux-android \
    --enable-static --disable-shared \
    --disable-fontconfig --disable-require-system-font-provider >/dev/null
  make -j"$NUCLEOS" >/dev/null
  make install >/dev/null
fi

# ---- 5. ffmpeg -------------------------------------------------------------
bajar https://github.com/FFmpeg/FFmpeg.git ffmpeg "$VER_FFMPEG"
echo "  → ffmpeg (esto tarda)"
cd "$TRABAJO/ffmpeg"

# -pie: Android exige ejecutables PIE desde API 21.
# Las librerías de arriba se enlazan estáticas; libc, libm y libdl salen del
# sistema, que en Android siempre están.
./configure \
  --prefix="$PREFIJO" \
  --target-os=android \
  --arch=aarch64 \
  --enable-cross-compile \
  --cc="$CC" --cxx="$CXX" --ar="$AR" --ranlib="$RANLIB" --nm="$NM" --strip="$STRIP" \
  --sysroot="$TOOLCHAIN/sysroot" \
  --extra-cflags="-O2 -fPIE -I$PREFIJO/include" \
  --extra-ldflags="-pie -L$PREFIJO/lib -static-libstdc++" \
  --pkg-config=pkg-config \
  --enable-gpl \
  --enable-version3 \
  --enable-libx264 \
  --enable-libass \
  --enable-libfreetype \
  --enable-libfribidi \
  --enable-protocol=file,pipe,concat \
  --enable-small \
  --disable-doc \
  --disable-debug \
  --disable-shared \
  --enable-static \
  --disable-ffplay \
  --disable-avdevice \
  --disable-symver

make -j"$NUCLEOS"

cp ffmpeg "$SALIDA/ffmpeg"
cp ffprobe "$SALIDA/ffprobe"
"$STRIP" "$SALIDA/ffmpeg" "$SALIDA/ffprobe"

echo
echo "  Listos:"
ls -la "$SALIDA"
echo
echo "  Comprobación rápida (no se pueden ejecutar aquí: son de Android):"
file "$SALIDA/ffmpeg" || true
