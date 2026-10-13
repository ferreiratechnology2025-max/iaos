import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { ComputerRuntimeClient, type ComputerControl } from '../data/computer/ComputerRuntimeClient';
import { canTake } from '../data/computer/takeAvailability';
import { statusMessage } from '../data/computer/statusMessage';
import { createHumanInputSender, createKeyForwarder, createPasteForwarder, mouseMessage } from '../data/computer/humanInput';

const runtime = new ComputerRuntimeClient();

function statusLabel(control: ComputerControl | null, streamReady: boolean): string {
  if (!streamReady) return 'Preparando controle…';
  return control?.holder === 'human' ? 'Você controla' : 'Bot controla';
}

export function ComputerPanel() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<WebSocket | null>(null);
  const frameRef = useRef({ width: 1, height: 1 });
  const [control, setControl] = useState<ComputerControl | null>(null);
  const [streamReady, setStreamReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try { if (alive) setControl(await runtime.readControl()); }
      catch { if (alive) setError('Computador indisponível'); }
    };
    const timer = window.setInterval(poll, 1000);
    void poll();
    const stream = runtime.connectStream((message) => {
      if (!alive) return;
      if (message.type === 'error') {
        setStreamReady(false);
        setError(message.error ?? 'Live screen indisponível');
        return;
      }
      if (message.type !== 'frame' || !message.data || message.width <= 0 || message.height <= 0) return;
      const canvas = canvasRef.current;
      if (!canvas) return;
      frameRef.current = { width: message.width, height: message.height };
      // Assigning width/height clears the bitmap even when unchanged; resize only on real change
      // so async image decode never leaves a blank canvas between frames (flicker).
      if (canvas.width !== message.width) canvas.width = message.width;
      if (canvas.height !== message.height) canvas.height = message.height;
      const image = new Image();
      image.onload = () => canvas.getContext('2d')?.drawImage(image, 0, 0);
      image.src = `data:image/jpeg;base64,${message.data}`;
      setStreamReady(true);
      setError(null);
    }, () => { if (alive) setStreamReady(false); });
    streamRef.current = stream;
    return () => { alive = false; window.clearInterval(timer); stream.close(); streamRef.current = null; };
  }, []);

  const humanReady = control?.holder === 'human' && streamReady;
  const humanReadyRef = useRef(false);
  humanReadyRef.current = humanReady;
  // Single gated path to the socket: nothing leaves unless the human holds the wheel.
  const send = useRef(createHumanInputSender(() => streamRef.current, () => humanReadyRef.current, WebSocket.OPEN)).current;
  const mouse = (kind: 'pressed' | 'released' | 'moved') => (event: MouseEvent<HTMLCanvasElement>) => {
    if (kind !== 'moved') event.preventDefault();
    send(mouseMessage(kind, event, event.currentTarget.getBoundingClientRect(), frameRef.current));
  };

  // The canvas cannot hold focus, so keystrokes are captured on window while the human drives.
  useEffect(() => {
    if (!humanReady) return;
    const forwarder = createKeyForwarder(send);
    window.addEventListener('keydown', forwarder.onKeyDown, true);
    window.addEventListener('keyup', forwarder.onKeyUp);
    const onPaste = createPasteForwarder(send);
    window.addEventListener('paste', onPaste);
    return () => {
      window.removeEventListener('keydown', forwarder.onKeyDown, true);
      window.removeEventListener('keyup', forwarder.onKeyUp);
      window.removeEventListener('paste', onPaste);
    };
  }, [humanReady, send]);
  const action = async (kind: 'take' | 'release') => {
    const requestId = control?.request?.id;
    if (kind === 'release' && !requestId) return;
    try { setControl(await (kind === 'take' ? runtime.takeControl() : runtime.releaseControl(requestId as string))); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'computer_control_rejected'); }
  };

  return <section className="computer-panel" aria-label="Computer">
    <div className="computer-head">
      <div><span className="eyebrow">COMPUTER</span><h2>Live browser</h2></div>
      <span className={`computer-state ${humanReady ? 'human' : ''}`}>{statusLabel(control, streamReady)}</span>
    </div>
    <div className={`computer-screen ${humanReady ? 'human-ready' : 'stream-waiting'}`}>
      <canvas ref={canvasRef}
        onMouseMove={mouse('moved')}
        onMouseDown={mouse('pressed')}
        onMouseUp={mouse('released')} />
      {!streamReady && <span className="computer-empty">Aguardando live screen…</span>}
    </div>
    <div className="computer-controls">
      <div><strong>Estado: {statusLabel(control, streamReady)}</strong><small>{statusMessage(control, streamReady)}</small></div>
      {control?.holder === 'human' ? <button type="button" onClick={() => void action('release')} disabled={!control.request?.id}>Devolver</button> : <button type="button" onClick={() => void action('take')} disabled={!canTake(control, streamReady)}>Assumir</button>}
    </div>
    {error && <p className="computer-error">{error}</p>}
  </section>;
}
