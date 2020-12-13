using System.Collections;
using System.Collections.Generic;
using UnityEngine;

[CreateAssetMenu(menuName = "Flock/Behavior/Boundary")]
public class BoundaryBehavior : FilteredFlockBehavior
{
    public Vector3 center;
    public float radius = 20f;
    public override Vector3 CalculateMove(Flock flock, FlockAgent agent, List<Transform> context)
    {
        var offset = center - agent.transform.position;
        var ratio = offset.magnitude / radius;
        if (ratio < 0.9f)
        {
            return Vector3.zero;
        }

        return ratio * ratio * offset;
    }
}
