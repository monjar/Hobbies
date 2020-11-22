class Constraint {
    constructor(p1, p2) {
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
        this.resolve = () => {
            const hDist = this.point1.x - this.point2.x;
            const vDist = this.point1.y - this.point2.y;
            const dist = Math.sqrt(hDist * hDist + vDist * vDist);
            const springFactor = (this.defaultDistant - dist) / dist;
            if (dist > maxDistance)
                this.point1.removeConstraint(this);
            const hSpringFactor = hDist * springFactor * 0.5;
            const vSpringFactor = vDist * springFactor * 0.5;
            this.point1.x += hSpringFactor;
            this.point1.y += vSpringFactor;
            this.point2.x -= hSpringFactor;
            this.point2.y -= vSpringFactor;
        };
        this.draw = () => {
            ctx.moveTo(this.point1.x, this.point1.y);
            ctx.lineTo(this.point2.x, this.point2.y);
        };
        this.point1 = p1;
        this.point2 = p2;
        this.defaultDistant = springLength;
    }
}
//# sourceMappingURL=constraints.js.map