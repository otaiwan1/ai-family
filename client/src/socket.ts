import { io } from 'socket.io-client';
import { getAccessToken } from './auth';

const socket = io({
  autoConnect: false,
  transports: ['websocket']
});

export function connectAuthenticatedSocket() {
  socket.auth = { token: getAccessToken() };
  if (!socket.connected) socket.connect();
}

export default socket;
