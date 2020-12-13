using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using Random = UnityEngine.Random;

public class Flock : MonoBehaviour
{
    public FlockAgent agentPrefab;
    public FlockBehavior behavior;
    [Range(2, 1000)] public int flockPopulation;
    [Range(1f, 100f)] public float speedMultiplier = 10f;
    [Range(1f, 100f)] public float maxSpeed = 5f;
    [Range(1f, 10f)] public float neighborRadius = 1.5f;
    [Range(0f, 1f)] public float avoidanceRadiusMultiplier = 0.5f;

    private const float Density = 0.08f;
    private List<FlockAgent> _flockAgents = new List<FlockAgent>();
    private float _sqMaxSpeed;
    private float _sqNeighborRadius;
    private float _sqAvoidanceRadius;

    public float SqAvoidanceRadius => _sqAvoidanceRadius;


    private void Start()
    {
        _sqMaxSpeed = maxSpeed * maxSpeed;
        _sqNeighborRadius = neighborRadius * neighborRadius;
        _sqAvoidanceRadius = _sqNeighborRadius * avoidanceRadiusMultiplier * avoidanceRadiusMultiplier;

        for (var i = 0; i < flockPopulation; i++)
            CreateAgent(i);
    }

    private void CreateAgent(int index)
    {
        var agentsPosition = flockPopulation * Density * Random.insideUnitSphere;
        var agent = Instantiate(agentPrefab, agentsPosition, Random.rotation, transform);
        agent.name = $"Agent{index}";
        _flockAgents.Add(agent);
    }

    private void Update()
    {
        _flockAgents.ForEach(agent =>
        {
            var context = GetNearbyObjects(agent);
            print(context.Count);
            var movement = behavior.CalculateMove(this, agent, context);
            movement *= speedMultiplier;
            if (movement.sqrMagnitude > _sqMaxSpeed)
                movement = movement.normalized * maxSpeed;
            agent.Move(movement);
        });
    }

    private readonly Collider[] _overlapResults = new Collider[200];

    private List<Transform> GetNearbyObjects(FlockAgent agent)
    {
        var context = new List<Transform>();
        var foundCount = Physics.OverlapBoxNonAlloc(agent.transform.position,
            new Vector3(neighborRadius, neighborRadius, neighborRadius), _overlapResults);
        for (var i = 0; i < foundCount; i++)
        {
            if (!_overlapResults[i].Equals(agent.AgentCollider))
                context.Add(_overlapResults[i].transform);
        }

        return context;
    }
}