"use strict";

const artifact = document.querySelector("#artifact");
const status = document.querySelector("#status");
const metadata = document.querySelector("#metadata");
const players = document.querySelector("#players");
const attempts = document.querySelector("#attempts");

artifact.addEventListener("change", async () => {
  players.replaceChildren();
  metadata.textContent = "";
  attempts.textContent = "";
  try {
    const selected = artifact.files[0];
    if (!selected) {
      status.textContent = "Choose a replay file";
      return;
    }
    const replay = JSON.parse(await selected.text());
    if (
      replay === null ||
      replay.schema_version !== 1 ||
      replay.kind !== "wiring-check" ||
      replay.status !== "complete" ||
      !Number.isSafeInteger(replay.seed) ||
      replay.seed < 0 ||
      replay.seed >= 2 ** 32 ||
      !Array.isArray(replay.frames) ||
      replay.frames.length !== 2 ||
      !Array.isArray(replay.replies) ||
      replay.replies.length !== 2
    ) {
      throw new Error("Unsupported or incomplete wiring artifact");
    }
    const frame = replay.frames[1];
    if (
      frame === null ||
      frame.tick !== 1 ||
      !Array.isArray(frame.players) ||
      frame.players.length !== 10
    ) {
      throw new Error("Invalid saved frame");
    }
    const seen = new Set();
    const circles = [];
    for (const player of frame.players) {
      if (
        player === null ||
        ![0, 1].includes(player.team_id) ||
        !Number.isInteger(player.player_id) ||
        player.player_id < 0 ||
        player.player_id > 4 ||
        !player.position ||
        !Number.isFinite(player.position.x) ||
        !Number.isFinite(player.position.y) ||
        Math.abs(player.position.x) > 55 ||
        Math.abs(player.position.y) > 35
      ) {
        throw new Error("Invalid saved player");
      }
      const identity = `${player.team_id}:${player.player_id}`;
      if (seen.has(identity)) throw new Error("Duplicate saved player");
      seen.add(identity);
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("cx", String((player.position.x + 55) * 6));
      circle.setAttribute("cy", String((35 - player.position.y) * 6));
      circle.setAttribute("r", "9");
      circle.setAttribute("fill", player.team_id === 0 ? "#60a5fa" : "#fb923c");
      circles.push(circle);
    }
    players.replaceChildren(...circles);
    metadata.textContent = `seed ${replay.seed} / tick ${frame.tick} / wiring-check v1`;
    attempts.textContent = JSON.stringify(replay.replies, null, 2);
    status.textContent = "Wiring check complete";
  } catch (error) {
    players.replaceChildren();
    status.textContent = `Replay error: ${error.message}`;
  }
});
