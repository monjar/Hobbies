using System.Collections;
using System.Collections.Generic;
using UnityEngine;


[CreateAssetMenu(menuName = "Flock/Behavior/SmoothCohesion")]
public class SmoothCohesionBehavior : FilteredFlockBehavior
{
    Vector3 currentVelocity;
    public float smoothTime= 0.5f;

    public override Vector3 CalculateMove(Flock flock, FlockAgent agent, List<Transform> context)
    {
        var movement = Vector3.zero;
        var agentPos = agent.transform.position;
        var filteredContext = (filter == null) ? context : filter.Filter(agent, context);
        if (filteredContext.Count == 0)
            return Vector3.zero;
        filteredContext.ForEach(c => movement += (c.position - agentPos));
        movement /= filteredContext.Count;
        movement = Vector3.SmoothDamp(agent.transform.forward, movement, ref currentVelocity, smoothTime);
        
       
        return movement;
    }
}