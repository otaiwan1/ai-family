const { io } = require("socket.io-client");
const socket = io("http://localhost:8000");

socket.on("connect", () => {
  console.log("Connected");
});
socket.on("host_state_update", (data) => {
  console.log("Received host_state_update, questions length:", data?.questions?.length);
  process.exit(0);
});
socket.on("disconnect", () => {
  console.log("Disconnected");
});

setTimeout(() => {
  console.log("Timeout");
  process.exit(1);
}, 3000);
