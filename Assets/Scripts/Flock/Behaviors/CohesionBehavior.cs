using System.Collections;
using System.Collections.Generic;
using UnityEngine;


[CreateAssetMenu(menuName = "Flock/Behavior/Cohesion")]
public class CohesionBehavior : FilteredFlockBehavior
{
    public override Vector3 CalculateMove(Flock flock, FlockAgent agent, List<Transform> context)
    {
      
        var movement = Vector3.zero;
        var agentPos = agent.transform.position;
        var filteredContext = (filter == null) ? context : filter.Filter(agent, context);
          if (filteredContext.Count == 0)
            return Vector3.zero;
        filteredContext.ForEach(c => movement += (c.position - agentPos));
        movement /= filteredContext.Count;
        return movement;
    }
}