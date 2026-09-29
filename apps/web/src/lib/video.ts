/**
 * Раскладка видеозаписи на кадры прямо в браузере.
 *
 * Видео целиком на сервер не уезжает: запись может быть часовой, а по делу нужны редкие кадры.
 * Браузер умеет декодировать всё, что умеет проигрывать, поэтому здесь нет ни одной библиотеки:
 * видео перематывается по секундам, кадр рисуется на холсте и ужимается в JPEG.
 */

export interface ExtractedFrames {
  /** Кадры по порядку, каждый — готовый JPEG. */
  frames: Blob[];
  /** Секунда записи для каждого кадра. */
  seconds: number[];
  /** Размер кадра, в котором считались координаты привязки. */
  width: number;
  height: number;
  /** Первый кадр: по нему человек отмечает углы пола. */
  preview: string;
  /** Длительность записи целиком. */
  duration: number;
}

export interface ExtractOptions {
  /** Сколько кадров в секунду брать из записи. */
  fps?: number;
  /** До какой ширины ужимать кадр. */
  maxWidth?: number;
  /** Сколько кадров максимум (сервис принимает 180). */
  limit?: number;
  onProgress?: (done: number, total: number) => void;
}

const DEFAULTS = { fps: 1, maxWidth: 480, limit: 180 };

/** Ждём события у элемента или отваливаемся по таймеру: битое видео не должно вешать страницу. */
function once(target: HTMLVideoElement, event: string, timeout = 15000): Promise<void> {
  return new Promise((resolve, reject) => {
    const done = () => {
      target.removeEventListener(event, done);
      target.removeEventListener('error', fail);
      clearTimeout(timer);
      resolve();
    };
    const fail = () => {
      target.removeEventListener(event, done);
      target.removeEventListener('error', fail);
      clearTimeout(timer);
      reject(new Error('Не удалось прочитать видео: браузер не понимает этот формат.'));
    };
    const timer = setTimeout(fail, timeout);
    target.addEventListener(event, done);
    target.addEventListener('error', fail);
  });
}

export async function extractFrames(file: File, options: ExtractOptions = {}): Promise<ExtractedFrames> {
  const { fps, maxWidth, limit } = { ...DEFAULTS, ...options };
  const url = URL.createObjectURL(file);
  const video = document.createElement('video');
  video.preload = 'auto';
  video.muted = true;
  video.playsInline = true;
  video.src = url;

  try {
    await once(video, 'loadedmetadata');
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    if (!duration) throw new Error('У записи не читается длительность — попробуйте другой файл.');

    const scale = Math.min(1, maxWidth / video.videoWidth);
    const width = Math.round(video.videoWidth * scale);
    const height = Math.round(video.videoHeight * scale);
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Браузер не дал холст для разбора видео.');

    // Кадры берутся равномерно: если запись длинная, шаг растягивается, чтобы уложиться в предел.
    const wanted = Math.min(limit, Math.max(8, Math.floor(duration * fps)));
    const step = duration / wanted;
    const frames: Blob[] = [];
    const seconds: number[] = [];
    let preview = '';

    for (let index = 0; index < wanted; index += 1) {
      const moment = Math.min(duration - 0.01, index * step);
      video.currentTime = moment;
      await once(video, 'seeked');
      context.drawImage(video, 0, 0, width, height);
      if (!preview) preview = canvas.toDataURL('image/jpeg', 0.9);
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.7));
      if (blob) {
        frames.push(blob);
        seconds.push(Number(moment.toFixed(2)));
      }
      options.onProgress?.(index + 1, wanted);
    }
    if (frames.length < 8) throw new Error('Из записи вышло меньше восьми кадров — нужна запись подлиннее.');
    return { frames, seconds, width, height, preview, duration };
  } finally {
    URL.revokeObjectURL(url);
    video.src = '';
  }
}
