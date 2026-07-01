"""共享常量：whicc.py 和 audio.py 的音频协议参数"""

SEG_DIR = "/tmp/whicc-seg"
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 4  # float32
SEG_DURATION_SEC = 1.0  # 每个段文件对应秒数
SEG_BYTES = int(SAMPLE_RATE * BYTES_PER_SAMPLE * SEG_DURATION_SEC)  # 64000 bytes
# 系统音频 tap 静默超时：健康的 tap 即便静音也持续输出零字节流,完全无数据
# 超过这个秒数 = tap 已死(切换默认输出设备的常见诱因)。
SYSTEM_AUDIO_STALL_SEC = 5.0
