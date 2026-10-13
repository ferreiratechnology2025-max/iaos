export type ComputerControl = {
  holder: 'bot' | 'human';
  requested: boolean;
  transitioning?: boolean;
  reason?: string;
  request?: { id: string; status: string; reason: string };
};

type FrameMessage = { type: 'frame'; data: string; width: number; height: number };

export class ComputerRuntimeClient {
  async readControl(): Promise<ComputerControl> {
    const response = await fetch('/computer/control', { cache: 'no-store' });
    if (!response.ok) throw new Error('computer_control_unavailable');
    return response.json();
  }

  async takeControl(): Promise<ComputerControl> {
    // The backend reuses a waiting request or creates one; the browser never names it.
    return this.post('/computer/take', {});
  }

  async releaseControl(requestId: string): Promise<ComputerControl> {
    return this.post('/computer/release', { requestId });
  }

  connectStream(onMessage: (message: FrameMessage | { type: 'error'; error?: string }) => void, onClose: () => void): WebSocket {
    const stream = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/computer/stream`);
    stream.onmessage = (event) => {
      try { onMessage(JSON.parse(event.data)); } catch { onMessage({ type: 'error', error: 'computer_stream_invalid_message' }); }
    };
    stream.onclose = onClose;
    stream.onerror = () => onMessage({ type: 'error', error: 'computer_stream_unavailable' });
    return stream;
  }

  private async post(path: string, body: object): Promise<ComputerControl> {
    const response = await fetch(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    const value = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(value.errorCode || 'computer_control_rejected');
    return value;
  }
}
