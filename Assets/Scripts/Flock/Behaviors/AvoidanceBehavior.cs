using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;

[CreateAssetMenu(menuName = "Flock/Behavior/Avoidance")]
public class AvoidanceBehavior : FlockBehavior
{
    public override Vector3 CalculateMove(Flock flock, FlockAgent agent, List<Transform> context)
    {
        if (context.Count == 0)
            return Vector3.zero;
        var movement = Vector3.zero;
        var agentPos = agent.transform.position;
        var inAvoidCount = 0;
        
        context.ForEach(c =>
        {
            if (Vector3.SqrMagnitude(c.position - agentPos) < flock.SqAvoidanceRadius)
            {
                inAvoidCount++;
                movement += (agentPos - c.position);
            }
        });
        if (inAvoidCount > 0)
            movement /= inAvoidCount;
        Debug.Log("heree " + movement);
        return movement;
    }
}