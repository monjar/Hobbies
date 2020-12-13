using System.Collections;
using System.Collections.Generic;
using UnityEngine;


[CreateAssetMenu(menuName = "Flock/Behavior/Composite")]
public class CompositeBehavior : FilteredFlockBehavior
{

    public FlockBehavior[] behaviors;
    public float[] weights;

    
    
    public override Vector3 CalculateMove(Flock flock, FlockAgent agent, List<Transform> context)
    {
        if (weights.Length != behaviors.Length)
        {
            Debug.LogError("Behavoirs and weights don't match");
            return Vector3.zero;
        }

        var movement = Vector3.zero;
        
        for(var i = 0; i < behaviors.Length; i++)
        {
            var moveTemp = behaviors[i].CalculateMove(flock, agent, context) * weights[i];

            if (moveTemp != Vector3.zero)
            {
                if (moveTemp.sqrMagnitude > weights[i] * weights[i])
                {
                    moveTemp.Normalize();
                    moveTemp *= weights[i];
                }

                movement += moveTemp;
            }
        }

        return movement;
    }
}
