const container = document.getElementById("waveform");

const setupCamera = () => {
  const camera = new THREE.PerspectiveCamera(
    55,
    window.innerWidth / window.innerHeight,
    1,
    400000
  );
  camera.position.z = 10000;
  camera.position.y = 10000;
  camera.lookAt(new THREE.Vector3(0, 9500, 0));
  return camera;
};

const setupScene = () => {
  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(config.backgroundColor, 1, 300000);
  return scene;
};

const setupRenderer = () => {
  const renderer = new THREE.WebGLRenderer();
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setClearColor(config.backgroundColor, 1);

  container.appendChild(renderer.domElement);
  return renderer;
};

const onWindowResize = () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
};

const main = () => {
  const renderer = setupRenderer();
  const scene = setupScene();
  const camera = setupCamera();
  const [planeGeo, planeMesh] = initWavePlane(
    config.planeDefinition,
    config.planeSize,
    config.meshColor,
    config.vertexHeight
  );
  scene.add(planeMesh);
  let counter = 0;
  const render = () => {
    requestAnimationFrame(render);
    counter = makeWave(planeGeo, planeMesh, counter);
    renderer.render(scene, camera);
  };
  render();
};

main();

window.addEventListener("resize", onWindowResize, false);
