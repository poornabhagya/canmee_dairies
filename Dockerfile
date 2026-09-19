# ==========================================
# STAGE 1: Builder (කම්පයිල් කිරීමේ අදියර)
# ==========================================
FROM python:3.12-slim-bookworm AS builder

# Python cache files හැදෙන එක නවත්වන්න
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# OS-level build headers සහ compilers ඉන්ස්ටෝල් කිරීම
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    default-libmysqlclient-dev \
    libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*

# Python libraries ඉන්ස්ටෝල් කිරීම (isolated /usr/local)
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/usr/local -r requirements.txt
# Gunicorn සහ Celery අනිවාර්ය නිසා ඒවත් මෙතනම දාගන්නවා
RUN pip install --no-cache-dir --prefix=/usr/local gunicorn redis celery


# ==========================================
# STAGE 2: Runtime (ප්‍රධාන සර්වර් එකේ දුවන අදියර)
# ==========================================
FROM python:3.12-slim-bookworm

# Threading Restrictions: Graviton2 එකේ CPU Thrashing වැළැක්වීමට
ENV OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# අත්‍යවශ්‍ය static runtime libraries පමණක් දැමීම (compilers නෑ)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmariadb3 \
    libjpeg62-turbo \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Stage 1 එකෙන් build කරපු Python packages ටික විතරක් copy කරගැනීම
COPY --from=builder /usr/local /usr/local

# අපේ කෝඩ් එක ඇතුළට copy කරගැනීම
COPY . /app

# Unprivileged system user කෙනෙක් (canmee:1000) හදලා permissions දීම
RUN useradd -u 1000 -U -s /bin/bash -m canmee && \
    mkdir -p /app/collected_static /app/media && \
    chown -R canmee:canmee /app

# Root user ගෙන් අයින් වෙලා සාමාන්‍ය user ගෙන් run වීම
USER canmee

EXPOSE 8000

# Gunicorn හරහා App එක Run කිරීම (Worker settings docker-compose එකෙන් දෙනවා)
CMD ["gunicorn", "canmee_dairies.wsgi:application", "--bind", "0.0.0.0:8000"]