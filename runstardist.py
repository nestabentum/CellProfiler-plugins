import os

from skimage.transform import resize

from stardist.models import StarDist2D
from csbdeep.utils import normalize

from cellprofiler_core.image import Image
from cellprofiler_core.module.image_segmentation import ImageSegmentation
from cellprofiler_core.object import Objects
from cellprofiler_core.setting import Binary
from cellprofiler_core.setting.choice import Choice
from cellprofiler_core.setting.do_something import DoSomething
from cellprofiler_core.setting.text import Integer, ImageName, Directory

GREY_1 = 'Versatile (fluorescent nuclei)'
GREY_2 = 'DSB 2018 (from StarDist 2D paper)'
COLOR_1 = 'Versatile (H&E nuclei)'
MODEL_CUSTOM = "Custom"
MODEL_OPTIONS = [GREY_1, COLOR_1, GREY_2, MODEL_CUSTOM]

__doc__ = f"""\
RunStardist
===========

**RunStarDist** uses a pre-trained machine learning model (StarDist) to detect cells or nuclei in an image.
This module is useful for automating simple segmentation tasks in CellProfiler.
The module takes in input images and produces an object set. Probabilities can also be captured as an image.

Loading in a model will take slightly longer the first time you run it each session. When evaluating 
performance you may want to consider the time taken to predict subsequent images.

Installation:
This can be a little tricky because of some dependency issues. We need to take care to not break CellProfiler's 
components when adding stardist to the environment.

You'll want to run `pip install --no-deps csbdeep` first to grab the cbsdeep package without installing an invalid 
version of h5py (CellProfiler needs h5py 3+). Following this run `pip install tensorflow stardist` to install other 
dependencies.

If using the pre-trained models, StarDist will download each when first used.

The models will automatically run on a GPU if compatible hardware is available and you have the required software. 
A guide to setting up Tensorflow GPU integration can be found at this link: https://www.tensorflow.org/install/gpu

|

============ ============ ===============
Supports 2D? Supports 3D? Respects masks?
============ ============ ===============
YES          YES          NO
============ ============ ===============

"""

MODE_CELLS = "Cells"
MODE_NUCLEI = "Nuclei"


class RunStarDist(ImageSegmentation):
    category = "Object Processing"

    module_name = "RunStarDist"

    variable_revision_number = 1

    def create_settings(self):
        super(RunStarDist, self).create_settings()

        self.model = Choice(
            text="Model",
            choices=MODEL_OPTIONS,
            value=GREY_1,
            doc="""\
StarDist comes with models for detecting nuclei. Alternatively, you can supply a custom-trained model 
generated outside of CellProfiler within Python. Custom models can be useful if working with unusual cell types.

The inbuilt fluorescent and DSB models expect greyscale images. The H&E model expects a color image as input (from 
brightfield). Custom models will require images of the type they were trained with. It should be noted that the 
models supplied with StarDist do not support 3D images, but it's possible to train and use your own.
""",
        )

        self.tile_image = Binary(
            text="Tile input image?",
            value=False,
            doc="""\
If enabled, the input image will be broken down into overlapping tiles. 
This can help to conserve memory when working with large images.

The image is split into a set number of vertical and horizontal tiles. 
The total number of tiles will be the result of multiplying the horizontal 
and vertical tile number.""",
        )

        self.n_tiles_x = Integer(
            text="Horizontal tiles",
            value=1,
            minval=1,
            doc="""\
Specify the number of tiles to break the image down into along the x-axis (horizontal)."""
        )

        self.n_tiles_y = Integer(
            text="Vertical tiles",
            value=1,
            minval=1,
            doc="""\
Specify the number of tiles to break the image down into along the y-axis (vertical)."""
        )

        self.save_probabilities = Binary(
            text="Save probability image?",
            value=False,
            doc="""
If enabled, the probability scores from the model will be recorded as a new image. 
Probability scales from 0-1, with 1 representing absolute certainty of a pixel being in a cell. 
You may want to use a custom threshold to manually generate objects.""",
        )

        self.probabilities_name = ImageName(
            "Name the probability image",
            "Probabilities",
            doc="Enter the name you want to call the probability image produced by this module.",
        )

        self.model_directory = Directory(
            "Model folder",
            doc=f"""\
*(Used only when using a custom pre-trained model)*

Select the folder containing your StarDist model. This should have the config, threshold and weights files 
exported after training."""
        )

        self.gpu_test = DoSomething(
            "",
            "Test GPU",
            self.do_check_gpu,
            doc=f"""\
Press this button to check whether a GPU is correctly configured.

If you have a dedicated GPU, a failed test usually means that either your GPU does not support deep learning or the 
required dependencies are not installed. 
Make sure you followed the setup instructions here: https://www.tensorflow.org/install/gpu

If you don't have a GPU or it's not configured, StarDist will instead run on the CPU. 
This will be slower but should work on any system.
""",
        )

    def settings(self):
        return [
            self.x_name,
            self.model,
            self.y_name,
            self.tile_image,
            self.n_tiles_x,
            self.n_tiles_y,
            self.save_probabilities,
            self.probabilities_name,
            self.model_directory,
        ]

    def visible_settings(self):
        vis_settings = [self.x_name, self.model]

        if self.model.value == MODEL_CUSTOM:
            vis_settings += [self.model_directory]

        vis_settings += [self.y_name, self.save_probabilities]

        if self.save_probabilities.value:
            vis_settings += [self.probabilities_name]

        vis_settings += [self.tile_image]
        if self.tile_image.value:
            vis_settings += [self.n_tiles_x, self.n_tiles_y]

        vis_settings += [self.gpu_test]

        return vis_settings

    def run(self, workspace):
        images = workspace.image_set
        x = images.get_image(self.x_name.value)
        dimensions = x.dimensions
        x_data = x.pixel_data

        # Validate some settings
        if self.model.value in (GREY_1, GREY_2) and x.multichannel:
            raise ValueError("Color images are not supported by this model. Please provide greyscale images.")
        elif self.model.value == COLOR_1 and not x.multichannel:
            raise ValueError("Greyscale images are not supported by this model. Please provide a color overlay.")

        if self.model.value != MODEL_CUSTOM:
            if x.volumetric:
                raise NotImplementedError("StarDist's inbuilt models do not currently support 3D images")
            model = StarDist2D.from_pretrained(self.model.value)
        else:
            model_directory, model_name = os.path.split(self.model_directory.get_absolute_path())
            if x.volumetric:
                from stardist.models import StarDist3D
                model = StarDist3D(config=None, basedir=model_directory, name=model_name)
            else:
                model = StarDist2D(config=None, basedir=model_directory, name=model_name)

        tiles = None
        if self.tile_image.value:
            tiles = []
            if x.volumetric:
                tiles += [1]
            tiles += [self.n_tiles_x.value, self.n_tiles_y.value]
            # Handle colour channels
            tiles += [1] * max(0, x.pixel_data.ndim - len(tiles))
            print(x.pixel_data.shape, x.pixel_data.ndim, tiles)

        if not self.save_probabilities.value:
            # Probabilities aren't wanted, things are simple
            data = model.predict_instances(
                normalize(x.pixel_data),
                return_predict=False,
                n_tiles=tiles
            )
            y_data = data[0]
        else:
            data, probs = model.predict_instances(
                normalize(x.pixel_data),
                return_predict=True,
                sparse=False,
                n_tiles=tiles
            )
            y_data = data[0]

            # Scores aren't at the same resolution as the input image.
            # We need to slightly resize to match the original image.
            size_corrected = resize(probs[0], y_data.shape)
            prob_image = Image(
                size_corrected,
                parent_image=x.parent_image,
                convert=False,
                dimensions=len(size_corrected.shape),
            )

            workspace.image_set.add(self.probabilities_name.value, prob_image)

            if self.show_window:
                workspace.display_data.probabilities = size_corrected

        y = Objects()
        y.segmented = y_data
        y.parent_image = x.parent_image
        objects = workspace.object_set
        objects.add_objects(y, self.y_name.value)

        self.add_measurements(workspace)

        if self.show_window:
            workspace.display_data.x_data = x_data
            workspace.display_data.y_data = y_data
            workspace.display_data.dimensions = dimensions

    def display(self, workspace, figure):
        if self.save_probabilities.value:
            layout = (2, 2)
        else:
            layout = (2, 1)

        figure.set_subplots(
            dimensions=workspace.display_data.dimensions, subplots=layout
        )

        figure.subplot_imshow(
            colormap="gray",
            image=workspace.display_data.x_data,
            title="Input Image",
            x=0,
            y=0,
        )

        figure.subplot_imshow_labels(
            image=workspace.display_data.y_data,
            sharexy=figure.subplot(0, 0),
            title=self.y_name.value,
            x=1,
            y=0,
        )
        if self.save_probabilities.value:
            figure.subplot_imshow(
                colormap="gray",
                image=workspace.display_data.probabilities,
                sharexy=figure.subplot(0, 0),
                title=self.probabilities_name.value,
                x=0,
                y=1,
            )

    def do_check_gpu(self):
        import tensorflow
        if len(tensorflow.config.list_physical_devices('GPU')) > 0:
            message = "GPU appears to be working correctly!"
            print("GPUs:", tensorflow.config.list_physical_devices('GPU'))
        else:
            message = "GPU test failed. There may be something wrong with your configuration."
        import wx
        wx.MessageBox(message, caption="GPU Test")
