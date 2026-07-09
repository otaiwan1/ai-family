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

export async function playCorrectSound(): Promise<void> {
  const context = getAudioContext();
  if (!context) return;
  await unlockGameAudio();
  if (context.state !== 'running') return;

  const start = context.currentTime + 0.01;
  const master = context.createGain();
  const compressor = context.createDynamicsCompressor();
  compressor.threshold.value = -18;
  compressor.knee.value = 8;
  compressor.ratio.value = 4;
  master.gain.value = 0.82;
  master.connect(compressor).connect(context.destination);

  const partials = [
    { ratio: 1, volume: 0.28, decay: 1.15 },
    { ratio: 2.01, volume: 0.13, decay: 0.72 },
    { ratio: 3.92, volume: 0.065, decay: 0.44 },
    { ratio: 5.43, volume: 0.035, decay: 0.28 },
  ];
  const fundamental = 1046.5;
  partials.forEach(({ ratio, volume, decay }, index) => {
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(fundamental * ratio, start);
    oscillator.detune.value = index % 2 === 0 ? -2 : 2;
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(volume, start + 0.002);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + decay);
    oscillator.connect(gain).connect(master);
    oscillator.start(start);
    oscillator.stop(start + decay + 0.02);
  });

  const transientDuration = 0.018;
  const transientBuffer = context.createBuffer(1, context.sampleRate * transientDuration, context.sampleRate);
  const transientData = transientBuffer.getChannelData(0);
  for (let index = 0; index < transientData.length; index += 1) {
    transientData[index] = Math.random() * 2 - 1;
  }
  const transient = context.createBufferSource();
  const transientFilter = context.createBiquadFilter();
  const transientGain = context.createGain();
  transient.buffer = transientBuffer;
  transientFilter.type = 'highpass';
  transientFilter.frequency.value = 3200;
  transientGain.gain.setValueAtTime(0.12, start);
  transientGain.gain.exponentialRampToValueAtTime(0.0001, start + transientDuration);
  transient.connect(transientFilter).connect(transientGain).connect(master);
  transient.start(start);
}

export async function playStrikeSound(): Promise<void> {
  const context = getAudioContext();
  if (!context) return;
  await unlockGameAudio();
  if (context.state !== 'running') return;

  const start = context.currentTime + 0.01;
  const duration = 0.48;
  const master = context.createGain();
  const highpass = context.createBiquadFilter();
  const lowpass = context.createBiquadFilter();
  highpass.type = 'highpass';
  highpass.frequency.setValueAtTime(130, start);
  lowpass.type = 'lowpass';
  lowpass.frequency.setValueAtTime(1800, start);
  master.gain.setValueAtTime(0.0001, start);
  master.gain.exponentialRampToValueAtTime(0.24, start + 0.004);
  master.gain.setValueAtTime(0.24, start + 0.36);
  master.gain.exponentialRampToValueAtTime(0.0001, start + duration);
  highpass.connect(lowpass).connect(master).connect(context.destination);

  [188, 194].forEach((frequency, index) => {
    const oscillator = context.createOscillator();
    oscillator.type = index === 0 ? 'square' : 'sawtooth';
    oscillator.frequency.setValueAtTime(frequency, start);
    oscillator.connect(highpass);
    oscillator.start(start);
    oscillator.stop(start + duration);
  });
}
