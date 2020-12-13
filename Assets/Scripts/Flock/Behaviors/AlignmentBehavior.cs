using System.Collections;
using System.Collections.Generic;
using UnityEngine;

public class AlignmentBehavior : FilteredFlockBehavior
{
    Vector3 currentVelocity;
    public float smoothTime= 0.2f;
    public override Vector3 CalculateMove(Flock flock, FlockAgent agent, List<Transform> context)
    {
        if (context.Count == 0)
            return agent.transform.forward;
        var movement = Vector3.zero;
        var filteredContext = (filter == null) ? context : filter.Filter(agent, context);
        if (filteredContext.Count == 0)
            return agent.transform.forward;
        filteredContext.ForEach(c => movement += c.transform.forward);
        movement /= filteredContext.Count;
        return movement;
    }
}
