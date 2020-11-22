// global variables (Forgive me please, I'm too tired) to store world's physics properties
const physicsIteration = 4, mouseEffect = 20, gravity = 9.8;
// global variables (Forgive me please, I'm too tired) to store UI interaction properties
let canvas, ctx, cloth, boundsx, boundsy, mouse = {
    down: false,
    button: 1,
    currentX: 0,
    currentY: 0,
    prevX: 0,
    prevY: 0,
};
//updates world (calls itself and re-renders the canvas so every iteration of this function is equal to 1 frame)
const update = () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    cloth.update();
    cloth.draw();
    requestAnimationFrame(update);
};
// Initializes the world and JS contex-canvas
const start = () => {
    initMouse();
    canvas.oncontextmenu = (e) => {
        e.preventDefault();
    };
    boundsx = canvas.width - 1;
    boundsy = canvas.height - 1;
    ctx.strokeStyle = "grey";
    cloth = new Cloth();
    update();
};
// This function adds functionallities to mouse
const initMouse = () => {
    canvas.onmousedown = (e) => {
        mouse.button = e.which;
        mouse.prevX = mouse.currentX;
        mouse.prevY = mouse.currentY;
        const rect = canvas.getBoundingClientRect();
        (mouse.currentX = e.clientX - rect.left),
            (mouse.currentY = e.clientY - rect.top),
            (mouse.down = true);
        e.preventDefault();
    };
    canvas.onmouseup = (e) => {
        mouse.down = false;
        e.preventDefault();
    };
    canvas.onmousemove = (e) => {
        mouse.prevX = mouse.currentX;
        mouse.prevY = mouse.currentY;
        const rect = canvas.getBoundingClientRect();
        (mouse.currentX = e.clientX - rect.left),
            (mouse.currentY = e.clientY - rect.top),
            e.preventDefault();
    };
};
// This is called on refresh of the html file (Kinda like the main of the program)
window.onload = () => {
    canvas = document.getElementById("c");
    ctx = canvas.getContext("2d");
    canvas.width = 1500;
    canvas.height = 1000;
    start();
};
//# sourceMappingURL=main.js.map