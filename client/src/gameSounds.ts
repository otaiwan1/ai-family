let audioContext: AudioContext | null = null;

function getAudioContext(): AudioContext | null {
  if (typeof window === 'undefined') return null;
  audioContext ??= new AudioContext();
  return audioContext;
}

export async function unlockGameAudio(): Promise<boolean> {
  const context = getAudioContext();
  if (!context) return false;
  if (context?.state === 'suspended') {
    try {
      await context.resume();
    } catch {
      return false;
    }
  }
  return context.state === 'running';
}

function scheduleTone(
  context: AudioContext,
  frequency: number,
  startTime: number,
  duration: number,
  volume: number,
  type: OscillatorType,
): void {
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.type = type;
  oscillator.frequency.setValueAtTime(frequency, startTime);
  gain.gain.setValueAtTime(0.0001, startTime);
  gain.gain.exponentialRampToValueAtTime(volume, startTime + 0.015);
  gain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);
  oscillator.connect(gain).connect(context.destination);
  oscillator.start(startTime);
  oscillator.stop(startTime + duration + 0.02);
}

export async function playCorrectSound(): Promise<void> {
  const context = getAudioContext();
  if (!context) return;
  await unlockGameAudio();
  if (context.state !== 'running') return;

  const start = context.currentTime + 0.01;
  const notes = [523.25, 659.25, 783.99];
  notes.forEach((frequency, index) => {
    const noteStart = start + index * 0.105;
    scheduleTone(context, frequency, noteStart, 0.28, 0.2, 'sine');
    scheduleTone(context, frequency * 2, noteStart, 0.18, 0.06, 'triangle');
  });
  scheduleTone(context, 1046.5, start + 0.32, 0.4, 0.12, 'sine');
}

export async function playStrikeSound(): Promise<void> {
  const context = getAudioContext();
  if (!context) return;
  await unlockGameAudio();
  if (context.state !== 'running') return;

  const start = context.currentTime + 0.01;
  const duration = 0.82;
  const master = context.createGain();
  const filter = context.createBiquadFilter();
  filter.type = 'lowpass';
  filter.frequency.setValueAtTime(900, start);
  master.gain.setValueAtTime(0.0001, start);
  master.gain.exponentialRampToValueAtTime(0.3, start + 0.02);
  master.gain.setValueAtTime(0.3, start + 0.55);
  master.gain.exponentialRampToValueAtTime(0.0001, start + duration);
  filter.connect(master).connect(context.destination);

  [116, 123].forEach((frequency, index) => {
    const oscillator = context.createOscillator();
    oscillator.type = index === 0 ? 'sawtooth' : 'square';
    oscillator.frequency.setValueAtTime(frequency, start);
    oscillator.frequency.exponentialRampToValueAtTime(frequency * 0.64, start + duration);
    oscillator.connect(filter);
    oscillator.start(start);
    oscillator.stop(start + duration);
  });

  const noiseBuffer = context.createBuffer(1, context.sampleRate * duration, context.sampleRate);
  const noiseData = noiseBuffer.getChannelData(0);
  for (let index = 0; index < noiseData.length; index += 1) {
    noiseData[index] = (Math.random() * 2 - 1) * Math.exp(-index / (context.sampleRate * 0.16));
  }
  const noise = context.createBufferSource();
  const noiseGain = context.createGain();
  noise.buffer = noiseBuffer;
  noiseGain.gain.setValueAtTime(0.14, start);
  noiseGain.gain.exponentialRampToValueAtTime(0.0001, start + 0.42);
  noise.connect(noiseGain).connect(context.destination);
  noise.start(start);
}
