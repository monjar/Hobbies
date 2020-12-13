using System.Collections;
using System.Collections.Generic;
using UnityEngine;

[CreateAssetMenu(menuName = "Flock/Filter/FlockMate")]
public class FlockMateFilter : ContextFilter
{
    public override List<Transform> Filter(FlockAgent agent, List<Transform> original)
    {
        return original.FindAll(t =>
        {
            var itemAgent = t.GetComponent<FlockAgent>();
            return itemAgent != null && itemAgent.AgentFlock == agent.AgentFlock;
        });
    }
}