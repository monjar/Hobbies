using System.Collections;
using System.Collections.Generic;
using UnityEngine;



[RequireComponent(typeof(Collider))]
public class FlockAgent : MonoBehaviour
{
    private Flock _agentFlock;
    public Flock AgentFlock => _agentFlock;
    
    private Collider _agentCollider;
    public Collider AgentCollider => _agentCollider;
    
    private void Start()
    {
        _agentCollider = GetComponent<Collider>();
    }

    public void Initialize(Flock flock)
    {
        _agentFlock = flock;
    }

    public void Move(Vector3 velocity)
    {
        if(velocity==Vector3.zero)
            return;
        transform.forward = velocity;
        transform.position += velocity * Time.deltaTime;
    }
}
