using System.Collections;
using System.Collections.Generic;
using UnityEngine;

[CreateAssetMenu(menuName = "Flock/Filter/Physics")]
public class PhysicsFilter : ContextFilter
{
    public LayerMask mask;

    public override List<Transform> Filter(FlockAgent agent, List<Transform> original)
    {
        var filtered =  original.FindAll(t => mask == (mask | (1 << t.gameObject.layer)));
       
        return filtered;
    }
}