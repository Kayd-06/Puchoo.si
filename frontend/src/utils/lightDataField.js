// Light-only data constellation; shared canvas lifecycle handles pause and budgets.
export function paintLightDataField(context, { width, height, time, staticMode, pointer }) {
  const count = staticMode ? 18 : width < 720 ? 34 : 64;
  const reach = width < 720 ? 90 : 145;
  // Continuous edge currents make motion legible even between sparse node pulses.
  if (!staticMode) {
    for (let lane = 0; lane < 6; lane += 1) {
      const right = lane % 2 === 1;
      const phase = (((time * 0.00009 + lane / 6) % 1) + 1) % 1;
      const centerY = phase * (height + 180) - 90;
      const baseX = width * (right ? 0.92 : 0.08);
      for (let segment = 0; segment < 18; segment += 1) {
        const y = centerY + segment * 4;
        const x = baseX + Math.sin(y * 0.009 + lane) * Math.min(48, width * 0.035);
        const nextY = y + 4;
        const nextX = baseX + Math.sin(nextY * 0.009 + lane) * Math.min(48, width * 0.035);
        context.beginPath();
        context.moveTo(x, y);
        context.lineTo(nextX, nextY);
        context.lineWidth = 1.2;
        context.strokeStyle = `hsla(35, 18%, 34%, ${(1 - segment / 18) * 0.35})`;
        context.stroke();
      }
    }
  }
  const nodes = Array.from({ length: count }, (_, index) => {
    const seedX = Math.sin(index * 127.1 + 19.7) * 43758.5453;
    const seedY = Math.sin(index * 311.7 + 73.3) * 28657.2719;
    const u = seedX - Math.floor(seedX);
    const v = seedY - Math.floor(seedY);
    const x = u * width + Math.sin(time * 0.00022 + index * 1.7) * 25;
    const y = v * height + Math.cos(time * 0.00019 + index) * 20;
    const edge = Math.min(1, Math.abs(u - 0.5) * 2 + Math.abs(v - 0.5));
    return { x, y, edge, index };
  });
  for (let a = 0; a < nodes.length; a += 1) {
    const node = nodes[a];
    for (let b = a + 1; b < nodes.length; b += 1) {
      const other = nodes[b];
      const distance = Math.hypot(node.x - other.x, node.y - other.y);
      if (distance >= reach) continue;
      context.beginPath();
      context.moveTo(node.x, node.y);
      context.lineTo(other.x, other.y);
      context.lineWidth = 0.7;
      context.strokeStyle = `hsla(35, 14%, 38%, ${(1 - distance / reach) * (0.06 + node.edge * 0.2)})`;
      context.stroke();
      // A sparse packet travels along selected links, rather than lighting every edge.
      if (!staticMode && (a * 7 + b) % 3 === 0) {
        const progress = (((time * 0.00028 + a * 0.17 + b * 0.31) % 1) + 1) % 1;
        const envelope = Math.sin(progress * Math.PI);
        const x = node.x + (other.x - node.x) * progress;
        const y = node.y + (other.y - node.y) * progress;
        context.beginPath();
        context.moveTo(x, y);
        context.lineTo(
          node.x + (other.x - node.x) * Math.max(0, progress - 0.08),
          node.y + (other.y - node.y) * Math.max(0, progress - 0.08),
        );
        context.lineWidth = 1.2;
        context.strokeStyle = `hsla(35, 18%, 36%, ${envelope * node.edge * 0.65})`;
        context.stroke();
        context.beginPath();
        context.arc(x, y, 2.2, 0, Math.PI * 2);
        context.fillStyle = `hsla(35, 18%, 36%, ${envelope * node.edge * 0.8})`;
        context.fill();
      }
    }
    const near = pointer?.active
      ? Math.max(0, 1 - Math.hypot(node.x - pointer.x, node.y - pointer.y) / 160)
      : 0;
    const pulse = 0.75 + Math.sin(time * 0.001 + node.index) * 0.25;
    context.fillStyle = `hsla(35, 14%, 33%, ${(0.15 + node.edge * 0.3 + near * 0.2) * pulse})`;
    context.beginPath();
    context.arc(node.x, node.y, node.index % 7 === 0 ? 2.1 : 1.15, 0, Math.PI * 2);
    context.fill();
    // Rings expand at a few outer nodes, fading before they reach the reading area.
    if (!staticMode && node.index % 7 === 0 && node.edge > 0.55) {
      const phase = (((time * 0.00012 + node.index * 0.19) % 1) + 1) % 1;
      context.beginPath();
      context.arc(node.x, node.y, 5 + phase * 42, 0, Math.PI * 2);
      context.lineWidth = 0.7;
      context.strokeStyle = `hsla(35, 16%, 42%, ${Math.sin(phase * Math.PI) * (1 - phase) * 0.5})`;
      context.stroke();
    }
    // Sparse square markers add a technical data texture without star imagery.
    if (node.index % 9 === 0) context.fillRect(node.x + 6, node.y - 6, 1.5, 1.5);
  }
}
