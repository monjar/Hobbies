const clothHeight = 45, clothWidth = 50, springLength = 5, maxDistance = 35;
class Cloth {
    constructor() {
        this.makePoint = (x, y, xBase, yBase) => {
            const p = new Point(xBase + x * springLength, yBase + y * springLength);
            x != 0 && p.attachPoint(this.points[this.points.length - 1]);
            y == 0 && p.pinDown(p.x, p.y);
            y != 0 && p.attachPoint(this.points[x + (y - 1) * (clothWidth + 1)]);
            this.points.push(p);
        };
        this.update = () => {
            let i = physicsIteration;
            while (i--)
                this.points.forEach((p) => p.resolveConstraints());
            this.points.forEach((p) => p.update(0.016));
        };
        this.draw = () => {
            ctx.beginPath();
            this.points.forEach((p) => p.draw());
            ctx.stroke();
        };
        this.points = [];
        const xBase = canvas.width / 2 - (clothWidth * springLength) / 2;
        const yBase = 20;
        for (let y = 0; y <= clothHeight; y++)
            for (let x = 0; x <= clothWidth; x++)
                this.makePoint(x, y, xBase, yBase);
    }
}
//# sourceMappingURL=cloth.js.map