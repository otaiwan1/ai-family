const { io } = require("socket.io-client");

const baseUrl = process.env.SOCKET_URL || "http://localhost:8000";
const password = process.env.ACCESS_PASSWORD || "AI2026";

async function main() {
  const loginResponse = await fetch(`${baseUrl}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!loginResponse.ok) throw new Error(`Login failed: ${loginResponse.status}`);
  const { token } = await loginResponse.json();

  const socket = io(baseUrl, {
    transports: ["websocket"],
    auth: { token },
  });

  socket.on("connect", () => console.log("Connected with authenticated session"));
  socket.on("host_state_update", (data) => {
    console.log("Received host_state_update, questions length:", data?.questions?.length);
    socket.disconnect();
    process.exit(0);
  });
  socket.on("connect_error", (error) => {
    console.error("Socket authentication failed:", error.message);
    process.exit(1);
  });

  setTimeout(() => {
    console.error("Timeout");
    socket.disconnect();
    process.exit(1);
  }, 5000);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
