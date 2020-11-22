class Point {
    constructor(x, y, mass = 1) {
        /*Adds the given force to this point.*/
        this.add_force = (hForce, vForce) => {
            this.vx += hForce / this.mass;
            this.vy += vForce / this.mass;
        };
        /*  Updates the state of this point (Is called every frame)*/
        this.update = (delta) => {
            if (mouse.down) {
                this.handleMouseDown();
            }
            this.add_force(0, gravity * this.mass);
            const newX = this.x - (this.xDest - this.x) + this.vx * delta;
            const newY = this.y - (this.yDest - this.y) + this.vy * delta;
            this.xDest = this.x;
            this.yDest = this.y;
            this.x = newX;
            this.y = newY;
            this.vy = this.vx = 0;
        };
        this.draw = () => {
            this.constraints.forEach((c) => c.draw());
        };
        this.resolveConstraints = () => {
            if (this.xPin != null && this.yPin != null) {
                this.x = this.xPin;
                this.y = this.yPin;
                return;
            }
            this.constraints.forEach((c) => c.resolve());
            this.handleBoundsConstraints();
        };
        this.attachPoint = (point) => {
            this.constraints.push(new Constraint(this, point));
        };
        this.removeConstraint = (constraint) => {
            this.constraints.splice(this.constraints.indexOf(constraint), 1);
        };
        this.pinDown = (pinx, piny) => {
            this.xPin = pinx;
            this.yPin = piny;
        };
        this.handleMouseDown = () => {
            const hDist = this.x - mouse.currentX;
            const vDist = this.y - mouse.currentY;
            const dist = Math.sqrt(hDist * hDist + vDist * vDist);
            if (mouse.button == 1) {
                if (dist < mouseEffect) {
                    this.xDest = this.x - (mouse.currentX - mouse.prevX);
                    this.yDest = this.y - (mouse.currentY - mouse.prevY);
                }
            }
        };
        this.handleBoundsConstraints = () => {
            this.x > boundsx
                ? (this.x = 2 * boundsx - this.x)
                : 1 > this.x && (this.x = 2 - this.x);
            this.y < 1
                ? (this.y = 2 - this.y)
                : this.y > boundsy && (this.y = 2 * boundsy - this.y);
        };
        this.x = x;
        this.y = y;
        this.xDest = x;
        this.yDest = y;
        this.vx = 0;
        this.vy = 0;
        this.xPin = null;
        this.yPin = null;
        this.mass = mass;
        this.constraints = [];
    }
}
//# sourceMappingURL=point.js.map