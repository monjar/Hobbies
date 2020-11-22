class Constraint {
  point1: Point;
  point2: Point;
  defaultDistant: number;

  constructor(p1: Point, p2: Point) {
    this.point1 = p1;
    this.point2 = p2;
    this.defaultDistant = springLength;
  }

  /*
    The resolve function applies the elastic force between two points
    in the constraint.
    Here we use the most simple way to apply this force:
    1. Finding the distance between point1 and point2.
      (Variable "dist")
    2. Making up a force factor indicating how much the "spring" between point1 and point2 is pulled.
      (Variable "springFactor")
    3. Breaking down the force factor into a vertical and a horizontal factor and multiplying them by 
      vertical and horizontal times 0.5. (The 0.5 is because we are assuming that the spring is uniform 
        in mass and material, So the elastic force is applied equally to both sides of the spring )
      (Variables "hSpringFactor" and "vSpringFactor") 

    4. Simply applying the movement to our points.
  */
  public resolve = (): void => {
    const hDist: number = this.point1.x - this.point2.x;
    const vDist: number = this.point1.y - this.point2.y;
    const dist: number = Math.sqrt(hDist * hDist + vDist * vDist);
    const springFactor: number = (this.defaultDistant - dist) / dist;

    if (dist > maxDistance) this.point1.removeConstraint(this);

    const hSpringFactor: number = hDist * springFactor * 0.5;
    const vSpringFactor: number = vDist * springFactor * 0.5;

    this.point1.x += hSpringFactor;
    this.point1.y += vSpringFactor;
    this.point2.x -= hSpringFactor;
    this.point2.y -= vSpringFactor;
  };

  public draw = (): void => {
    ctx.moveTo(this.point1.x, this.point1.y);
    ctx.lineTo(this.point2.x, this.point2.y);
  };
}
