FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow \
    LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/usr/lib:/usr/local/lib \
    PYTHONPATH=/app

WORKDIR /app

# Runtime libs commonly needed by VTK/slicer-core in headless environments
RUN apt-get update && apt-get install -y --no-install-recommends \
    libegl1 \
    libgl1 \
    libglx-mesa0 \
    libglib2.0-0 \
    libosmesa6 \
    libsm6 \
    libx11-6 \
    libxcursor1 \
    libxext6 \
    libxrender1 \
    libturbojpeg0-dev \
    && rm -rf /var/lib/apt/lists/*

# PyTurboJPEG's auto-discovery is inconsistent across slim images. Create a stable
# unversioned soname in a common search path so the Python binding can always find it.
RUN ln -sf /usr/lib/x86_64-linux-gnu/libturbojpeg.so.0 /usr/lib/libturbojpeg.so

COPY . /app

RUN pip install --no-cache-dir -U pip \
    && pip uninstall -y turbojpeg || true \
    && pip install --no-cache-dir 'PyTurboJPEG<2' \
    && pip install --no-cache-dir -e '.[standalone]'

EXPOSE 8080

CMD ["python", "examples/medical_viewer_app.py", "--server", "--host", "0.0.0.0", "-p", "8080"]
