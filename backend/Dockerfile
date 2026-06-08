FROM python:3.11-slim

WORKDIR /app

# torch/soundfile/librosa need libgomp1 + libsndfile1 at runtime on Debian slim;
# ffmpeg/ffprobe themselves are NOT installed here — the app fetches and caches
# its own static binaries (imageio-ffmpeg + static-ffmpeg), so the image stays
# free of a system ffmpeg that could conflict with them.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Slow connections can take many minutes per chunk on a large wheel — pip's 15s
# default read timeout aborts long before that, so we raise it generously and
# add retries. PyPI's default `torch` wheel drags in the full CUDA stack
# (cuDNN/cuBLAS/etc — over a gigabyte) that a CPU-only container never uses;
# pulling CPU-only builds from PyTorch's own index avoids that entirely and
# keeps the image (and the download) an order of magnitude smaller. Installing
# them in their own layer also means a later failure on a smaller dependency
# doesn't force re-downloading them from scratch.
ENV PIP_DEFAULT_TIMEOUT=2000
COPY requirements.txt .
RUN pip install --no-cache-dir --retries 10 \
    --index-url https://download.pytorch.org/whl/cpu \
    torch==2.5.1 torchaudio==2.5.1
RUN pip install --no-cache-dir --retries 10 -r requirements.txt

COPY app ./app

# Without this, Python buffers stdout when it isn't a TTY — `docker compose
# logs` would then show nothing until the buffer happens to flush, which makes
# debugging a running container needlessly hard. Placed after the slow pip
# layers so changing it never invalidates that ~10-minute cache.
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
