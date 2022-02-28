const initWavePlane = (defenition, size, meshColor, vertexHeight) => {
  const planeGeo = new THREE.PlaneGeometry(size, size, defenition, defenition);
  const planeMesh = new THREE.Mesh(
    planeGeo,
    new THREE.MeshBasicMaterial({
      color: meshColor,
    })
  );
  planeMesh.rotation.x -= Math.PI * 0.5;
  for (var i = 0; i < planeGeo.vertices.length; i++) {
    planeGeo.vertices[i].z += Math.random() * vertexHeight - vertexHeight;
    planeGeo.vertices[i]._myZ = planeGeo.vertices[i].z;
  }
  return [planeGeo, planeMesh];
};

const makeWave = (planeGeo, planeMesh, counter) => {
  for (var i = 0; i < planeGeo.vertices.length; i++) {
    planeGeo.vertices[i].z =
      Math.sin(i + counter * 0.00002) *
      (planeGeo.vertices[i]._myZ - planeGeo.vertices[i]._myZ * 0.6);
    planeMesh.geometry.verticesNeedUpdate = true;
    counter += 0.06;
  }
  return counter;
};
