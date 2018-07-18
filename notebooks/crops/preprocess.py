import random
import os
import shutil
import copy
from skimage import measure
from skimage import morphology as skim
import skimage.io as skio
import warnings
import pandas as pd
import numpy as np
import pathlib
from difflib import SequenceMatcher
import yaml

import gdal # for gridding

random.seed(4)

def preprocess():
    
    def parse_yaml(input_file):
    """Parse yaml file of configuration parameters."""
    with open(input_file, 'r') as yaml_file:
        params = yaml.load(yaml_file)
    return params

    params = parse_yaml('preprocess_config.yaml') 

    ROOT = params['dirs']['root']

    DATASET = os.path.join(
        ROOT, params['dirs']['dataset'])

    REORDER = os.path.join(
        DATASET, params['dirs']['reorder'])

    TRAIN = os.path.join(
        DATASET, params['dirs']['train'])

    TEST = os.path.join(
        DATASET, params['dirs']['test'])

    GRIDDED_IMGS = os.path.join(
        DATASET, params['dirs']['gridded_imgs'])

    GRIDDED_LABELS = os.path.join(
        DATASET, params['dirs']['gridded_labels'])

    OPENED = os.path.join(
        DATASET, params['dirs']['opened'])

    INSTANCES = os.path.join(
        DATASET, params['dirs']['instances'])
    
    NEG_BUFFERED = os.path.join(
        DATASET, params['dirs']['neg_buffered_labels'])

    RESULTS = os.path.join(ROOT,'../',params['dirs']['results'], params['dirs']['dataset'])

    SOURCE_IMGS = os.path.join(
        ROOT, params['dirs']['source_imgs'])

    SOURCE_LABELS = os.path.join(
        ROOT, params['dirs']['source_labels'])

    dirs = [DATASET, REORDER, TRAIN, TEST, GRIDDED_IMGS, GRIDDED_LABELS, OPENED, INSTANCES, RESULTS]

    # Make directory and subdirectories
    for d in dirs:
        pathlib.Path(d).mkdir(parents=False, exist_ok=False)

    # Change working directory to project directory
    os.chdir(dirs[1])
    
    def remove_dir_folders(directory):
        '''
        Removes all files and sub-folders in a folder and keeps the folder.
        '''
    
        folderlist = [ f for f in os.listdir(directory)]
        for f in folderlist:
            shutil.rmtree(os.path.join(directory,f))
    
    def yaml_to_band_index(params):
        band_list = []
        for i, band in enumerate(params['bands_to_include']):
            if list(band.values())[0]== True:
                band_list.append(i)
        return band_list
    
    def reorder_images(params):
    """Load the os, gs, or both images and subset bands. Growing
    Season is stacked first before OS if both true.
    """
        file_ids_all = next(os.walk(SOURCE_IMGS))[2]
        band_indices = yaml_to_band_index(params)
        image_ids_gs = sorted([image_id for image_id in file_ids_all \
                               if 'GS' in image_id and '.aux' not in image_id])
        image_ids_os = sorted([image_id for image_id in file_ids_all \
                               if 'OS' in image_id and '.aux' not in image_id])

        if params['seasons']['GS'] and params['seasons']['OS'] == False:
            for img_path in image_ids_gs:
                gs_image = skio.imread(os.path.join(SOURCE_IMGS, img_path))
                gs_image = gs_image[:,:,band_indices]
                skio.imsave(gs_image_path, gs_image, plugin='tifffile')

        elif params['seasons']['OS'] and params['seasons']['GS'] == False:
            for img_path in image_ids_os:
                os_image = skio.imread(os.path.join(SOURCE_IMGS, img_path))
                os_image = gs_image[:,:,band_indices]
                skio.imsave(os_image_path, os_image, plugin='tifffile')
        else:
            for gs_path, os_path in zip(image_ids_gs, image_ids_os):
                print(gs_path, os_path)
                gs_image = skio.imread(os.path.join(SOURCE_IMGS, gs_path))
                os_image = skio.imread(os.path.join(SOURCE_IMGS, os_path))
                gsos_image = np.dstack([gs_image[:,:,band_indices], os_image[:,:,band_indices]])

                match = SequenceMatcher(None, gs_path, os_path).find_longest_match(0, len(gs_path), 0, len(os_path))
                path = gs_path[match.b: match.b + match.size] 
                # this may need to be reworked for diff file names
                # works best if unique ids like GS go in front of filename
                gsos_image_path = os.path.join(REORDER, path + 'OSGS.tif')
                skio.imsave(gsos_image_path, gsos_image, plugin='tifffile')

    def negative_buffer_and_small_filter(params):
        """
        Applies a negative buffer to wv2 labels since some are too close together and 
        produce conjoined instances when connected components is run (even after 
        erosion/dilation). This may not get rid of all conjoinments and should be adjusted.
        It relies too on the source projection of the label file to calculate distances for
        the negative buffer. Currently hardcodes in projections, need to look up utm pojection
        based on spatial location somehow if I'm to extend this to work with labels anywhere.

        Returns rasterized labels that are ready to be gridded
        """
        neg_buffer = float(params['label_vals']['neg_buffer'])
        small_area_filter = float(params['label_vals']['small_area_filter'])
        # This is a helper  used with sorted for a list of strings by specific indices in 
        # each string. Was used for a long path that ended with a file name
        # Not needed here but may be with different source imagery and labels
        # def takefirst_two(elem):
        #     return int(elem[-12:-10])

        items = os.listdir(SOURCE_LABELS)
        labels = []
        for name in items:
            if name.endswith(".shp"):
                labels.append(os.path.join(SOURCE_LABELS,name))  

        shp_list = sorted(labels)
        # need to use Source imagery for geotransform data for rasterized shapes, didn't preserve when save imgs to reorder
        scenes = os.listdir(SOURCE_IMGS)
        scenes = [scene for scene in scenes if 'GS' in scene]
        img_list = []
        for name in scenes:
            img_list.append(os.path.join(SOURCE_IMGS,name))  

        img_list = sorted(img_list)


        for shp_path, img_path in zip(shp_list, img_list):
            shp_frame = gpd.read_file(shp_path)
            with rasterio.open(img_path) as rast:
                meta = rast.meta.copy()
                meta.update(compress="lzw")
                meta['count'] = 1
            tifname = os.path.splitext(os.path.basename(shp_path))[0] + '.tif'
            rasterized_name = os.path.join(NEG_BUFFERED, tifname)
            with rasterio.open(rasterized_name, 'w+', **meta) as out:
                out_arr = out.read(1)
                # we get bounds to deterimine which projection to use for neg buffer
                shp_frame.loc[0,'DN'] = 0
                shp_frame.loc[1:,'DN'] = 1
                maxx_bound = shp_frame.bounds.maxx.max()
                minx_bound = shp_frame.bounds.minx.min()
                if maxx_bound >= 30 and minx_bound>= 30:
                    shp_frame = shp_frame.to_crs({'init': 'epsg:32736'})
                    shp_frame['geometry'] = shp_frame['geometry'].buffer(neg_buffer)
                    shp_frame['Shape_Area'] = shp_frame.area
                    shp_frame = shp_frame.to_crs({'init': 'epsg:4326'})

                else:
                    shp_frame = shp_frame.to_crs({'init': 'epsg:32735'})
                    shp_frame['geometry'] = shp_frame['geometry'].buffer(neg_buffer)
                    shp_frame['Shape_Area'] = shp_frame.area
                    shp_frame = shp_frame.to_crs({'init': 'epsg:4326'})

                # filtering out very small fields, in meters. 100 meters area looks like a good number for now
                shp_frame = shp_frame.loc[shp_frame.Shape_Area > small_area_filter]
                shp_frame = shp_frame[shp_frame.DN==1] # get rid of extent polygon
                # https://gis.stackexchange.com/questions/151339/rasterize-a-shapefile-with-geopandas-or-fiona-python#151861
                shapes = ((geom,value) for geom, value in zip(shp_frame.geometry, shp_frame.DN))
                burned = features.rasterize(shapes=shapes, fill=0, out=out_arr, transform=out.transform, default_value=1)
                burned[burned < 0] = 0
                out.write_band(1, burned) 
        print('Done applying negbuff of {negbuff} and filtering small labels of area less than {area}'.format(negbuff=neg_buffer,area=small_area_filter))          

    def rm_mostly_empty(scene_path, label_path):
        '''
        Removes a grid that is mostly (over 1/4th) empty and corrects bad no data value to 0.
        Ignor ethe User Warning, unsure why it pops up but doesn't seem to impact the array shape
        '''
        usable_data_threshold = params['image_vals']['usable_thresh']
        arr = skio.imread(scene_path)
        arr[arr<0] = 0
        skio.imsave(scene_path, arr)
        pixel_count = arr.shape[0] * arr.shape[1]
        nodata_pixel_count = (arr == 0).sum()
        if 1-(nodata_pixel_count/pixel_count) < usable_data_threshold:
            os.remove(scene_path)
            os.remove(label_path)
            print('removed scene and label, less than {}% good data'.format(usable_data_threshold))
            
    def grid_images(params):
        """
        Grids up imagery to a variable size. Filters out imagery with too little usable data.
        appends a random unique id to each tif and label pair, appending string 'label' to the 
        mask.
        """
        img_list = sorted(next(os.walk(REORDER))[2])
        label_list = sorted(next(os.walk(NEG_BUFFERED))[2])

        for img_name, label_name in zip(img_list, label_list):
            img_path = os.path.join(REORDER, img_name)
            label_path = os.path.join(NEG_BUFFERED, label_name)
            #assign unique name to each gridded tif, keeping season suffix
            #assigning int of same length as ZA0932324 naming convention

            tile_size_x = params['image_vals']['grid_size']
            tile_size_y = params['image_vals']['grid_size']
            ds = gdal.Open(img_path)
            band = ds.GetRasterBand(1)
            xsize = band.XSize
            ysize = band.YSize   

            for i in range(0, xsize, tile_size_x):
                for j in range(0, ysize, tile_size_y):
                    unique_id = str(random.randint(100000000,999999999))
                    out_path_img = os.path.join(GRIDDED_IMGS,unique_id)+ '.tif'
                    out_path_label = os.path.join(GRIDDED_LABELS,unique_id)+ '_label.tif'
                    com_string = "gdal_translate -of GTIFF -srcwin " + str(i)+ ", " + \
                        str(j) + ", " + str(tile_size_x) + ", " + str(tile_size_y) + " " + \
                        str(img_path) + " " + str(out_path_img)
                    os.system(com_string)
                    com_string = "gdal_translate -of GTIFF -srcwin " + str(i)+ ", " + \
                        str(j) + ", " + str(tile_size_x) + ", " + str(tile_size_y) + " " + \
                        str(label_path) + " " + str(out_path_label)
                    os.system(com_string)
                    rm_mostly_empty(out_path_img, out_path_label)

    def open_labels(params):
        negative_buffer_and_small_filter(params)
        k = params['label_vals']['kernel']
        if params['label_vals']['open'] == True:
            label_list = next(os.walk(NEG_BUFFERED))[2]

            for name in label_list:
                arr = skio.imread(os.path.join(NEG_BUFFERED,name))
                arr[arr < 0]=0
                opened_path = os.path.join(OPENED,name)
                kernel = np.ones((k,k))
                arr = skim.binary_opening(arr, kernel)
                arr=1*arr
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", category=UserWarning)
                    skio.imsave(opened_path, 1*arr)
            print('Done opening with kernel of height and width of {size}'.format(size=k))         
    
    def move_img_to_folder(params):
        '''Moves a file with identifier pattern 760165086_OSGS.tif to a 
        folder path ZA0165086/image/ZA0165086.tif
        Also creates a masks folder at ZA0165086/masks'''
        
        folder_name = os.path.join(TRAIN_DIR,filename[:9])
        if os.path.isdir(folder_name):
            shutil.rmtree(folder_name)
        os.mkdir(folder_name)
        new_path = os.path.join(folder_name, 'image')
        mask_path = os.path.join(folder_name, 'masks')
        os.mkdir(new_path)
        file_path = os.path.join(REORDERED_DIR,filename)
        os.rename(file_path, os.path.join(new_path, filename))
        os.mkdir(mask_path)

    for img in image_list:
        move_img_to_folder(img)

    label_list = next(os.walk(OPENED_LABELS_DIR))[2]
    # save connected components and give each a number at end of id
    for name in label_list:
        arr = skio.imread(os.path.join(OPENED_LABELS_DIR,name))
        blob_labels = measure.label(arr, background=0)
        blob_vals = np.unique(blob_labels)
        for blob_val in blob_vals[blob_vals!=0]:
            labels_copy = blob_labels.copy()
            assert labels_copy.shape == (512, 512)
            labels_copy[blob_labels!=blob_val] = 0
            labels_copy[blob_labels==blob_val] = 1
            label_name = name[0:15]+str(blob_val)+'.tif'
            label_path = os.path.join(CONNECTED_COMP_DIR,label_name)
            assert labels_copy.ndim == 2
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=UserWarning)
                skio.imsave(label_path, labels_copy)

    def move_mask_to_folder(filename):
        '''Moves a mask with identifier pattern ZA0165086_label_1.tif to a 
        folder path ZA0165086/mask/ZA0165086_label_1.tif. Need to run 
        connected components first.
        '''
        if os.path.isdir(os.path.join(TRAIN_DIR,filename[:9])):
            folder_path = os.path.join(TRAIN_DIR,filename[:9])
            mask_path = os.path.join(folder_path, 'masks')
            file_path = os.path.join(CONNECTED_COMP_DIR,filename)
            os.rename(file_path, os.path.join(mask_path, filename))

    mask_list = next(os.walk(CONNECTED_COMP_DIR))[2]
    for mask in mask_list:
        move_mask_to_folder(mask)

    id_list = next(os.walk(TRAIN_DIR))[1]
    
    for fid in id_list:
        mask_folder = os.path.join(DATASET_DIR, 'train',fid, 'masks')
        im_folder = os.path.join(DATASET_DIR, 'train',fid, 'image')
        if not os.listdir(mask_folder):
            im_path = os.path.join(im_folder, os.listdir(im_folder)[0])
            arr = skio.imread(im_path)
            assert arr.shape == (512, 512, 3)
            mask = np.zeros_like(arr[:,:,0])
            assert mask.shape == (512, 512)
            assert mask.ndim == 2
            # ignores warning about low contrast image
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=UserWarning)
                skio.imsave(os.path.join(mask_folder, fid + '_label_0.tif'),mask)

    def train_test_split(train_dir, test_dir, kprop):
        """Takes a sample of folder ids and copies them to a test directory
        from a directory with all folder ids. Each sample folder contains an 
        images and corresponding masks folder."""

        remove_dir_folders(test_dir)
        sample_list = next(os.walk(train_dir))[1]
        k = round(kprop*len(sample_list))
        test_list = random.sample(sample_list,k)
        for test_sample in test_list:
            shutil.copytree(os.path.join(train_dir,test_sample),os.path.join(test_dir,test_sample))
        train_list = list(set(next(os.walk(train_dir))[1]) - set(test_list))
        train_df = pd.DataFrame({'train': train_list})
        test_df = pd.DataFrame({'test': test_list})
        train_df.to_csv(os.path.join(RESULTS_DIR, 'train_ids.csv'))
        test_df.to_csv(os.path.join(RESULTS_DIR, 'test_ids.csv'))
        
    train_test_split(TRAIN_DIR, TEST_DIR, .1)
    print('preprocessing complete, ready to run model.')

def get_arr_channel_mean(channel):
    means = []
    for i, fid in enumerate(id_list):
        im_folder = os.path.join('train',fid, 'image')
        im_path = os.path.join(im_folder, os.listdir(im_folder)[0])
        arr = skio.imread(im_path)
        arr[arr==-1.7e+308]=np.nan
        means.append(np.nanmean(arr[:,:,channel]))
    return np.mean(means)