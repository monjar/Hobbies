using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;

[CreateAssetMenu(menuName = "Flock/Behavior/Avoidance")]
public class AvoidanceBehavior : FilteredFlockBehavior
{
    public override Vector3 CalculateMove(Flock flock, FlockAgent agent, List<Transform> context)
    {
        var movement = Vector3.zero;
        var agentPos = agent.transform.position;
        var inAvoidCount = 0;
        
        var filteredContext = (filter == null) ? context : filter.Filter(agent, context);
       
        filteredContext.ForEach(c =>
        {
            if (Vector3.SqrMagnitude(c.position - agentPos) < flock.SqAvoidanceRadius)
            {
                inAvoidCount++;
                movement += (agentPos - c.position);
            }
        });
        if (inAvoidCount > 0)
            movement /= inAvoidCount;
        return movement;
    }
}