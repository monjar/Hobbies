using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;

[CreateAssetMenu(menuName = "Flock/Behavior/Avoidance")]
public class AvoidanceBehavior : FilteredFlockBehavior
{
    
    
    Vector3 currentVelocity;
    public float smoothTime= 0.5f;
    public override Vector3 CalculateMove(Flock flock, FlockAgent agent, List<Transform> context)
    {
        var movement = Vector3.zero;
        var agentPos = agent.transform.position;
        var inAvoidCount = 0;
        
        var filteredContext = (filter == null) ? context : filter.Filter(agent, context);
       
        filteredContext.ForEach(c =>
        {
            Vector3 closestPoint = c.GetComponent<Collider>().ClosestPointOnBounds(agentPos);
            var dist = agentPos - closestPoint;
            if (Vector3.SqrMagnitude(dist) < flock.SqAvoidanceRadius)
            {
                
                inAvoidCount++;
                movement +=dist;
            }
        });
        if (inAvoidCount > 0)
            movement /= inAvoidCount;
        
        movement = Vector3.SmoothDamp(agent.transform.forward, movement, ref currentVelocity, smoothTime);
        return movement;
    }
}