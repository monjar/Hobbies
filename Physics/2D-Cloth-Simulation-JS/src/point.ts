class Point {
  x: number;
  y: number;
  xDest: number;
  yDest: number;
  vx: number;
  vy: number;
  xPin: number;
  yPin: number;
  mass: number;
  constraints: any[];

  constructor(x: number, y: number, mass: number = 1) {
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

  /*Adds the given force to this point.*/
  public add_force = (hForce: number, vForce: number): void => {
    this.vx += hForce / this.mass;
    this.vy += vForce / this.mass;
  };

  /*  Updates the state of this point (Is called every frame)*/
  public update = (delta: number): void => {
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

  public draw = (): void => {
    this.constraints.forEach((c: Constraint) => c.draw());
  };

  public resolveConstraints = (): void => {
    if (this.xPin != null && this.yPin != null) {
      this.x = this.xPin;
      this.y = this.yPin;
      return;
    }

    this.constraints.forEach((c: Constraint) => c.resolve());
    this.handleBoundsConstraints();
  };

  public attachPoint = (point: Point): void => {
    this.constraints.push(new Constraint(this, point));
  };
  public removeConstraint = (constraint: Constraint): void => {
    this.constraints.splice(this.constraints.indexOf(constraint), 1);
  };

  public pinDown = (pinx: number, piny: number): void => {
    this.xPin = pinx;
    this.yPin = piny;
  };

  private handleMouseDown = (): void => {
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
  private handleBoundsConstraints = (): void => {
    this.x > boundsx
      ? (this.x = 2 * boundsx - this.x)
      : 1 > this.x && (this.x = 2 - this.x);
    this.y < 1
      ? (this.y = 2 - this.y)
      : this.y > boundsy && (this.y = 2 * boundsy - this.y);
  };
}
