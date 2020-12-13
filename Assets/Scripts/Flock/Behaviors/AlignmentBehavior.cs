using System.Collections;
using System.Collections.Generic;
using UnityEngine;

[CreateAssetMenu(menuName = "Flock/Behavior/Alignment")]
public class AlignmentBehavior : FlockBehavior
{
    public override Vector3 CalculateMove(Flock flock, FlockAgent agent, List<Transform> context)
    {
        if (context.Count == 0)
            return agent.transform.forward;
        var movement = Vector3.zero;
        context.ForEach(c => movement += c.transform.forward);
        movement /= context.Count;
        return movement;
    }
}
