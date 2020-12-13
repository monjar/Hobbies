using System.Collections;
using System.Collections.Generic;
using UnityEngine;


[CreateAssetMenu(menuName = "Flock/Behavior/Cohesion")]
public class CohesionBehavior : FlockBehavior
{
    public override Vector3 CalculateMove(Flock flock, FlockAgent agent, List<Transform> context)
    {
        if (context.Count == 0)
            return Vector3.zero;
        var movement = Vector3.zero;
        var agentPos = agent.transform.position;
        context.ForEach(c => movement += (c.position - agentPos));
        movement /= context.Count;
        return movement;
    }
}